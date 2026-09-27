# -*- coding: utf-8 -*-
"""真实人类交互引擎：贝塞尔平滑鼠标轨迹、随机微抖动、拟人键盘节奏与微停留。

为 Camoufox/Playwright 提供高保真人机交互特征，规避 Cloudflare / xAI 行为遥测风控。
"""
from __future__ import annotations

import math
import random
import time
from typing import Any, Optional, Tuple


def _get_mouse_pos(raw_page: Any) -> Tuple[float, float]:
    """获取页面当前鼠标记录坐标；初次未记录时赋予视口上方自然落点。"""
    pos = getattr(raw_page, "_human_mouse_pos", None)
    if isinstance(pos, (list, tuple)) and len(pos) == 2:
        return float(pos[0]), float(pos[1])
    try:
        vp = raw_page.viewport_size or {"width": 1280, "height": 800}
        w, h = vp.get("width", 1280), vp.get("height", 800)
    except Exception:
        w, h = 1280, 800
    init_x = float(random.randint(int(w * 0.15), int(w * 0.40)))
    init_y = float(random.randint(int(h * 0.10), int(h * 0.35)))
    try:
        raw_page.mouse.move(init_x, init_y)
    except Exception:
        pass
    raw_page._human_mouse_pos = [init_x, init_y]
    return init_x, init_y


def _set_mouse_pos(raw_page: Any, x: float, y: float) -> None:
    raw_page._human_mouse_pos = [float(x), float(y)]


def human_move(
    raw_page: Any,
    target_x: float,
    target_y: float,
    steps: Optional[int] = None,
    speed: float = 1.0,
) -> None:
    """使用三阶贝塞尔曲线及 Ease-in-out 缓动算法，将鼠标平滑移动至目标点。"""
    if not raw_page:
        return
    x0, y0 = _get_mouse_pos(raw_page)
    dx = target_x - x0
    dy = target_y - y0
    dist = math.hypot(dx, dy)

    if dist < 3.0:
        try:
            raw_page.mouse.move(target_x, target_y)
            _set_mouse_pos(raw_page, target_x, target_y)
        except Exception:
            pass
        return

    # 根据欧氏距离计算步数
    if steps is None:
        if dist < 40:
            calc_steps = random.randint(8, 14)
        elif dist < 200:
            calc_steps = random.randint(16, 26)
        elif dist < 600:
            calc_steps = random.randint(28, 42)
        else:
            calc_steps = random.randint(45, 65)
        steps = int(max(6, calc_steps / max(0.2, speed)))

    # 法向量与弯曲幅度（模拟手臂/手腕生理弧度）
    perp_x = -dy / dist
    perp_y = dx / dist
    curve_amp = random.uniform(-1.0, 1.0) * min(90.0, max(12.0, dist * 0.22))

    # 生成两个贝塞尔控制点
    p1_ratio = random.uniform(0.20, 0.38)
    p2_ratio = random.uniform(0.62, 0.82)

    ctrl1_x = x0 + dx * p1_ratio + perp_x * curve_amp
    ctrl1_y = y0 + dy * p1_ratio + perp_y * curve_amp

    ctrl2_x = x0 + dx * p2_ratio + perp_x * (curve_amp * random.uniform(0.4, 0.85))
    ctrl2_y = y0 + dy * p2_ratio + perp_y * (curve_amp * random.uniform(0.4, 0.85))

    last_x, last_y = x0, y0
    for i in range(1, steps + 1):
        s = i / float(steps)
        # 余弦缓动曲线（两端慢、中间快）
        t = 0.5 * (1.0 - math.cos(math.pi * s))

        # 三阶贝塞尔方程
        u = 1.0 - t
        tt = t * t
        uu = u * u
        uuu = uu * u
        ttt = tt * t

        cur_x = uuu * x0 + 3.0 * uu * t * ctrl1_x + 3.0 * u * tt * ctrl2_x + ttt * target_x
        cur_y = uuu * y0 + 3.0 * uu * t * ctrl1_y + 3.0 * u * tt * ctrl2_y + ttt * target_y

        # 在中段叠加轻微手部生理震颤（接近终点时归零，确保精准命中）
        if 0.15 < s < 0.88:
            tremor = (1.0 - s) * random.uniform(-0.8, 0.8)
            cur_x += tremor
            cur_y += tremor

        try:
            raw_page.mouse.move(cur_x, cur_y)
        except Exception:
            break
        last_x, last_y = cur_x, cur_y

        # 步间间隔（模拟 60Hz-120Hz 刷新采样）
        step_delay = random.uniform(0.005, 0.013)
        time.sleep(step_delay)

    # 确保终点绝对对齐
    try:
        raw_page.mouse.move(target_x, target_y)
    except Exception:
        pass
    _set_mouse_pos(raw_page, target_x, target_y)


def human_click_locator(
    raw_page: Any,
    locator: Any,
    timeout: float = 30.0,
    delay: Optional[int] = None,
) -> bool:
    """移动鼠标至元素内部自然落点并执行拟人点击。"""
    if not locator or not raw_page:
        return False
    try:
        locator.scroll_into_view_if_needed(timeout=int(min(timeout, 4.0) * 1000))
    except Exception:
        pass

    box = None
    try:
        box = locator.bounding_box()
    except Exception:
        box = None

    if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
        # 命中在元素内部中央 60% 区域（避免机械命中绝对中心）
        bx = box["x"]
        by = box["y"]
        bw = box["width"]
        bh = box["height"]

        rel_x = bw * random.uniform(0.25, 0.75)
        rel_y = bh * random.uniform(0.25, 0.75)
        target_x = bx + rel_x
        target_y = by + rel_y

        # 贝塞尔平滑移动至目标点（注入高保真行为遥测轨迹）
        human_move(raw_page, target_x, target_y)

        # 视线对齐微停留
        time.sleep(random.uniform(0.08, 0.20))

        # 物理按压点击：使用 locator.click 并传入相对坐标与按下时长，保证 actionability 与表单事件完整性
        press_ms = delay if delay else random.randint(55, 115)
        try:
            clamped_rel_x = max(1.0, min(float(bw - 1.0), rel_x))
            clamped_rel_y = max(1.0, min(float(bh - 1.0), rel_y))
            locator.click(
                timeout=int(timeout * 1000),
                delay=press_ms,
                position={"x": clamped_rel_x, "y": clamped_rel_y},
            )
            time.sleep(random.uniform(0.04, 0.12))
            return True
        except Exception:
            pass

    # 无法获取包围盒时走常规点击兜底
    try:
        press_ms = delay if delay else random.randint(55, 120)
        locator.click(timeout=int(timeout * 1000), delay=press_ms)
        return True
    except Exception:
        return False


def human_type_locator(
    raw_page: Any,
    locator: Any,
    text: str,
    clear: bool = True,
    timeout: float = 30.0,
) -> bool:
    """拟人化键入：先点击聚焦，再模拟真实打字节奏输入。"""
    if not locator or not raw_page:
        return False

    # 先用拟人点击聚焦输入框
    clicked = human_click_locator(raw_page, locator, timeout=timeout)
    if not clicked:
        try:
            locator.focus(timeout=int(min(timeout, 3.0) * 1000))
        except Exception:
            pass
    else:
        try:
            locator.focus(timeout=int(min(timeout, 2.0) * 1000))
        except Exception:
            pass

    time.sleep(random.uniform(0.12, 0.25))

    # 若需要清空已有内容，模拟人类全选 + 删除（而非 JS 直接赋值清空）
    if clear:
        try:
            raw_page.keyboard.press("ControlOrMeta+A")
            time.sleep(random.uniform(0.06, 0.12))
            raw_page.keyboard.press("Backspace")
            time.sleep(random.uniform(0.06, 0.14))
        except Exception:
            try:
                locator.fill("")
            except Exception:
                pass

    # 逐字拟人打字
    chars = list(str(text or ""))
    for idx, ch in enumerate(chars):
        # 基础击键延迟
        base_delay = random.randint(45, 85)

        # 符号或特殊字符（@、.、-、_、! 等）增加视觉查找停留
        if ch in ("@", ".", "-", "_", "!", "+", "#"):
            time.sleep(random.uniform(0.14, 0.28))
            base_delay = random.randint(60, 105)
        # 连续打字节奏（偶尔微停顿 100-200ms，符合真人打字思考）
        elif idx > 0 and idx % random.randint(5, 8) == 0:
            if random.random() < 0.35:
                time.sleep(random.uniform(0.10, 0.20))

        try:
            locator.press_sequentially(ch, delay=base_delay)
        except Exception:
            try:
                raw_page.keyboard.type(ch, delay=base_delay)
            except Exception:
                pass

    # 输入完成后的短暂停留（核对输入内容）
    time.sleep(random.uniform(0.18, 0.40))
    return True


def human_mouse_drift(raw_page: Any, count: int = 2) -> None:
    """模拟人类在页面停留、阅读或等待验证码/页面加载时手部的轻微晃动。"""
    if not raw_page:
        return
    try:
        vp = raw_page.viewport_size or {"width": 1280, "height": 800}
        w, h = vp.get("width", 1280), vp.get("height", 800)
    except Exception:
        w, h = 1280, 800

    cur_x, cur_y = _get_mouse_pos(raw_page)
    for _ in range(max(1, count)):
        # 在当前鼠标位置附近 60-180px 范围内产生微小漫游
        angle = random.uniform(0, 2 * math.pi)
        drift_dist = random.uniform(40.0, 160.0)
        dest_x = max(60.0, min(float(w - 60), cur_x + math.cos(angle) * drift_dist))
        dest_y = max(60.0, min(float(h - 60), cur_y + math.sin(angle) * drift_dist))

        human_move(raw_page, dest_x, dest_y, speed=random.uniform(0.8, 1.3))
        cur_x, cur_y = dest_x, dest_y
        time.sleep(random.uniform(0.15, 0.40))
