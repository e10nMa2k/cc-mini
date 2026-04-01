"""32 badges + gacha draw system.

16 green (common), 8 purple (precious), 4 red (rare), 2 gold (legendary).
Draw costs 5 tickets; duplicates refund tickets.
"""
from __future__ import annotations

import random
from typing import TYPE_CHECKING

from .types import (
    Badge,
    BADGE_TIERS,
    DRAW_PROBABILITY,
    DUPLICATE_REFUND,
    TICKET_COST,
)

if TYPE_CHECKING:
    from .types import GameSession

# ---------------------------------------------------------------------------
# 32 硬编码徽章
# ---------------------------------------------------------------------------

ALL_BADGES: dict[str, Badge] = {}

def _b(bid: str, name: str, desc: str, tier: str, effect: str) -> Badge:
    badge = Badge(badge_id=bid, name=name, description=desc, tier=tier, effect=effect)
    ALL_BADGES[bid] = badge
    return badge

# ===== 16 绿色普通 (green) — 基础被动效果 =====
_b("green_01", "新芽之证",   "初次探索的纪念",           "green", "HP+5")
_b("green_02", "勇气之星",   "迈出第一步的勇气",         "green", "ATK+1")
_b("green_03", "拾荒者印记", "善于发现废墟中的宝物",     "green", "LCK+2")
_b("green_04", "林中密语",   "聆听森林的声音",           "green", "DEF+1")
_b("green_05", "蘑菇猎人",   "采集蘑菇的达人认证",       "green", "HP+5")
_b("green_06", "晨露之珠",   "收集清晨第一滴露水",       "green", "SPD+1")
_b("green_07", "旅人之靴",   "走过千里路的证明",         "green", "SPD+2")
_b("green_08", "矿工之锤",   "在洞穴中挥洒汗水",         "green", "ATK+2")
_b("green_09", "海风之歌",   "聆听海浪的旋律",           "green", "DEF+2")
_b("green_10", "齿轮碎片",   "从废墟中找到的精密零件",   "green", "ATK+1")
_b("green_11", "星尘瓶",     "装满星光的小瓶子",         "green", "LCK+2")
_b("green_12", "友谊纽带",   "与NPC结下的友情证明",      "green", "HP+5")
_b("green_13", "好奇心徽章", "探索未知的勇气",           "green", "LCK+1")
_b("green_14", "治愈之叶",   "森林精灵赐予的祝福",       "green", "HP+10")
_b("green_15", "铁壁之盾",   "坚定不移的防御意志",       "green", "DEF+2")
_b("green_16", "疾风之羽",   "被风暴洗礼的轻盈羽毛",    "green", "SPD+2")

# ===== 8 紫色珍贵 (purple) — 中等被动效果 =====
_b("purple_01", "暗影行者",   "在黑暗中自如行走的证明",   "purple", "SPD+5")
_b("purple_02", "水晶之眼",   "看穿幻象的洞察之眼",       "purple", "LCK+5")
_b("purple_03", "雷霆之印",   "承受雷电洗礼后的印记",     "purple", "ATK+5")
_b("purple_04", "深海之魂",   "与深海共鸣的灵魂",         "purple", "DEF+5")
_b("purple_05", "机关大师",   "破解复杂机关的智慧",       "purple", "ATK+3,DEF+3")
_b("purple_06", "元素共鸣",   "与自然元素产生共鸣",       "purple", "HP+15")
_b("purple_07", "月光祝福",   "月神的温柔祝福",           "purple", "HP+10,SPD+3")
_b("purple_08", "命运指引",   "命运之线若隐若现",         "purple", "LCK+8")

# ===== 4 红色稀有 (red) — 强力被动效果 =====
_b("red_01", "龙焰之心",     "征服龙焰的证明",           "red", "ATK+10")
_b("red_02", "不灭守护",     "永不倒下的意志",           "red", "DEF+8,HP+20")
_b("red_03", "时空裂隙",     "触碰时间裂缝的证明",       "red", "SPD+8,LCK+5")
_b("red_04", "万物低语",     "能听到万物声音的能力",     "red", "全属性+3")

# ===== 2 金色传说 (gold) — 极强被动效果 =====
_b("gold_01", "星辰之主",     "掌控星辰的传说存在",       "gold", "全属性+5")
_b("gold_02", "命运编织者",   "改写命运的传说存在",       "gold", "LCK+20")

# ---------------------------------------------------------------------------
# Badge pools by tier
# ---------------------------------------------------------------------------

BADGES_BY_TIER: dict[str, list[str]] = {tier: [] for tier in BADGE_TIERS}
for _bid, _badge in ALL_BADGES.items():
    BADGES_BY_TIER[_badge.tier].append(_bid)


# ---------------------------------------------------------------------------
# Draw logic
# ---------------------------------------------------------------------------

def _adjusted_draw_probs(lck: int) -> dict[str, float]:
    """Adjust draw probabilities based on LCK stat."""
    probs = dict(DRAW_PROBABILITY)
    bonus_purple = 0.0
    bonus_gold = 0.0
    if lck > 20:
        bonus_purple += 0.05
        bonus_gold += 0.01
    if lck > 40:
        bonus_purple += 0.05
        bonus_gold += 0.01
    probs["purple"] += bonus_purple
    probs["gold"] += bonus_gold
    probs["green"] -= (bonus_purple + bonus_gold)
    probs["green"] = max(probs["green"], 0.10)  # floor
    return probs


def draw_badge(session: GameSession) -> tuple[Badge | None, bool, int]:
    """Perform a gacha draw.

    Returns (badge, is_new, refund_tickets).
    Returns (None, False, 0) if not enough tickets.
    """
    if session.tickets < TICKET_COST:
        return None, False, 0

    session.tickets -= TICKET_COST

    # Roll tier
    probs = _adjusted_draw_probs(session.stats.get("LCK", 10))
    roll = random.random()
    cumulative = 0.0
    chosen_tier = "green"
    for tier in BADGE_TIERS:
        cumulative += probs.get(tier, 0)
        if roll < cumulative:
            chosen_tier = tier
            break

    # Pick random badge from tier
    pool = BADGES_BY_TIER[chosen_tier]
    badge_id = random.choice(pool)
    badge = ALL_BADGES[badge_id]

    # Check duplicate
    owned_ids = {b.badge_id for b in session.badges}
    if badge_id in owned_ids:
        refund = DUPLICATE_REFUND.get(chosen_tier, 0)
        session.tickets += refund
        return badge, False, refund

    # New badge
    session.badges.append(badge)
    return badge, True, 0


def badge_progress(session: GameSession) -> tuple[int, int]:
    """Return (owned, total) badge counts."""
    return len(session.badges), len(ALL_BADGES)
