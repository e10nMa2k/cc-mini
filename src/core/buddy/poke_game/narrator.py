"""LLM narrator — gpt-5-mini driven storytelling with fallback templates.

Uses a dedicated OpenAI LLMClient. When LLM is unavailable, falls back to
pre-written narrative templates so the game can run offline.
"""
from __future__ import annotations

import json
import random
import re
from typing import Any

from .types import GameSession, Item, Skill, NPC, ELEMENTS, RARITIES

# ---------------------------------------------------------------------------
# Fallback narrative templates (by region, ~40 total)
# ---------------------------------------------------------------------------

_TEMPLATES: dict[str, list[str]] = {
    "幽暗森林": [
        "{name}穿过茂密的灌木丛，脚下踩着松软的落叶。一束阳光穿透树冠，照亮了前方的小路。",
        "森林深处传来鸟儿清脆的歌声，{name}循着声音走去，发现了一片隐秘的空地。",
        "{name}在一棵古树下发现了奇怪的菌类，五颜六色地发着微光。空气中弥漫着甜蜜的香气。",
        "一只萤火虫在{name}面前飞舞，仿佛在引路。跟着它走了一段，发现了一个隐秘的角落。",
        "风吹过树梢，树叶沙沙作响。{name}感到一阵宁静，在{location}的庇护下继续前行。",
        "一只小松鼠从树上跳下，好奇地打量着{name}。它嘴里衔着什么闪亮的东西。",
        "{name}在溪边停下脚步，清澈的水面映照出森林的倒影。水中似乎有什么东西在闪烁。",
    ],
    "水晶洞穴": [
        "水晶折射出的光芒在洞壁上形成了彩虹般的光影，{name}被这奇景深深吸引。",
        "洞穴深处传来有节奏的滴水声，{name}沿着水晶通道小心翼翼地前行。",
        "{name}的脚步声在空旷的洞穴中回荡，前方的水晶簇散发着幽蓝色的冷光。",
        "一颗巨大的水晶柱突然迸发出刺眼的光芒，{name}不得不闭上眼睛。等光芒散去，周围的景象变了。",
        "{name}在{location}的深处发现了一面光滑如镜的水晶壁，上面映照出奇异的画面。",
        "洞穴中的气温突然降低，{name}呼出的气息在水晶的光芒中化作了小小的冰晶。",
    ],
    "风暴山脉": [
        "狂风呼啸着掠过山巅，{name}紧紧抓住岩壁，一步步向上攀登。",
        "闪电划破天际，照亮了整个山脉。{name}在雷声中发现了一个避风的岩洞。",
        "{name}站在{location}，俯瞰脚下翻涌的云海，感受到大自然的壮阔与渺小。",
        "山风中夹杂着奇异的能量粒子，{name}感到全身充满了力量。",
        "一头巨大的雷鹰从云层中俯冲而下，在{name}头顶盘旋了一圈后飞走了，留下一根闪电羽毛。",
        "山路愈发陡峭，但{name}的眼中闪烁着坚定的光芒。每一步都在变得更强。",
    ],
    "深海遗迹": [
        "气泡从{name}身旁缓缓上升，海底的世界比想象中更加绚丽多彩。",
        "一群发光的水母从{name}身边游过，将整个{location}照得如同梦境。",
        "{name}在珊瑚丛中发现了一个古老的箱子，上面刻满了神秘的海洋符文。",
        "海流突然变得湍急，{name}被卷向了一个未知的方向。等水流平息，眼前是一片新的景象。",
        "一条巨大的鲸鱼从远处游过，它的歌声在深海中回荡，{name}感到莫名的平静。",
        "{name}触碰了海神祭坛上的水晶球，一股温暖的能量流遍全身。",
    ],
    "机械废墟": [
        "齿轮转动的声音在空旷的废墟中回响，{name}踏过散落的零件，继续深入探索。",
        "一个古老的机器人突然启动，用沙哑的声音说了一串{name}听不懂的语言后又沉默了。",
        "{name}在{location}的角落发现了一个还在运转的小型装置，屏幕上闪烁着数据。",
        "管道中突然喷出蒸汽，{name}敏捷地闪开了。蒸汽散去后，露出了一条隐藏的通道。",
        "废墟中的照明系统突然恢复，灯光一盏接一盏地亮起，照亮了{name}前方的道路。",
        "{name}在数据终端上发现了一段古代程序，似乎记录着某种强大技术的原理。",
    ],
    "星光圣殿": [
        "漂浮的光球在{name}周围缓缓旋转，整个圣殿充满了庄严而神圣的气息。",
        "{name}踏上了纯白的大理石地板，每一步都伴随着清脆的回响和一圈圈扩散的光纹。",
        "星图缓慢旋转，某个星座突然闪耀了起来。{name}感到一股神秘的力量在召唤。",
        "{name}站在{location}，感受到无数星辰的注视。这里是整个世界最接近天空的地方。",
        "命运之池泛起了涟漪，{name}在水面上看到了一个模糊的影像——那似乎是未来的自己。",
        "王座散发的光芒变得更加耀眼，{name}感到自己的潜力被彻底唤醒。",
        "圣殿的天花板上，星辰如同活物般游动，{name}伸手触碰，指尖传来温暖的触感。",
    ],
}

# Flat list for generic fallback
_ALL_TEMPLATES: list[str] = []
for _tpls in _TEMPLATES.values():
    _ALL_TEMPLATES.extend(_tpls)

# ---------------------------------------------------------------------------
# Fallback item/skill pools
# ---------------------------------------------------------------------------

_FALLBACK_ITEMS: list[dict[str, str]] = [
    {"name": "生命草", "rarity": "common", "effect": "HP+10", "description": "恢复少量生命的草药"},
    {"name": "铁矿碎片", "rarity": "common", "effect": "DEF+1", "description": "普通的铁矿碎片"},
    {"name": "速度果实", "rarity": "common", "effect": "SPD+1", "description": "食用后身体变得轻盈"},
    {"name": "力量石", "rarity": "uncommon", "effect": "ATK+2", "description": "蕴含力量的矿石"},
    {"name": "幸运四叶草", "rarity": "uncommon", "effect": "LCK+2", "description": "据说能带来好运"},
    {"name": "水晶碎片", "rarity": "uncommon", "effect": "DEF+2", "description": "坚硬的水晶碎片"},
    {"name": "龙鳞", "rarity": "rare", "effect": "DEF+4", "description": "坚不可摧的龙鳞"},
    {"name": "凤凰之泪", "rarity": "rare", "effect": "HP+30", "description": "传说中凤凰的眼泪"},
    {"name": "雷电精华", "rarity": "epic", "effect": "ATK+6", "description": "凝聚的雷电之力"},
    {"name": "星尘", "rarity": "epic", "effect": "全属性+2", "description": "来自星辰的碎片"},
    {"name": "时间沙漏", "rarity": "legendary", "effect": "SPD+10", "description": "扭曲时间的神器"},
]

_FALLBACK_SKILLS: list[dict[str, Any]] = [
    {"name": "火球术", "power": 15, "element": "fire", "description": "发射一团小火球"},
    {"name": "治愈之光", "power": 10, "element": "light", "description": "温柔的治愈光芒"},
    {"name": "风之刃", "power": 20, "element": "wind", "description": "锋利的风之刃"},
    {"name": "岩石护盾", "power": 15, "element": "earth", "description": "坚固的岩石屏障"},
    {"name": "暗影步", "power": 25, "element": "shadow", "description": "在暗影中穿行"},
    {"name": "水之愈", "power": 20, "element": "water", "description": "流水般的治愈之力"},
    {"name": "雷霆一击", "power": 40, "element": "wind", "description": "凝聚雷电的强力一击"},
    {"name": "烈焰风暴", "power": 50, "element": "fire", "description": "召唤烈焰风暴"},
    {"name": "深渊凝视", "power": 60, "element": "shadow", "description": "来自深渊的力量"},
    {"name": "星辰之力", "power": 75, "element": "light", "description": "借用星辰的力量"},
]

# ---------------------------------------------------------------------------
# Narrator class
# ---------------------------------------------------------------------------

class Narrator:
    """LLM-driven narrator with fallback to templates."""

    _MODEL = "gpt-5-mini"
    _MAX_HISTORY = 20
    _SUMMARY_THRESHOLD = 15

    def __init__(self, companion_name: str, companion_species: str):
        self._name = companion_name
        self._species = companion_species
        self._messages: list[dict[str, str]] = []
        self._summary: str = ""
        self._client: Any = None
        self._llm_available = False
        self._init_client()

    def _init_client(self) -> None:
        """Try to create an OpenAI LLMClient."""
        try:
            from ...llm import LLMClient
            self._client = LLMClient(provider="openai")
            self._llm_available = True
        except Exception:
            self._llm_available = False

    def _system_prompt(self, session: GameSession) -> str:
        items_str = ", ".join(i.name for i in session.inventory[:10]) or "空"
        skills_str = ", ".join(s.name for s in session.skills[:10]) or "无"
        badges_owned, badges_total = len(session.badges), 32
        loc_name = session.location.name if session.location else "未知"
        region = session.location.region if session.location else "未知"

        summary_section = ""
        if self._summary:
            summary_section = f"\n[之前的冒险摘要]: {self._summary}\n"

        return (
            f"你是一个奇幻世界的叙事者，正在讲述{self._name}（一只{self._species}）的冒险故事。\n"
            f"当前位置: {loc_name}（{region}）\n"
            f"属性: HP={session.stats.get('HP',0)}, ATK={session.stats.get('ATK',0)}, "
            f"DEF={session.stats.get('DEF',0)}, SPD={session.stats.get('SPD',0)}, "
            f"LCK={session.stats.get('LCK',0)}\n"
            f"背包: {items_str}\n技能: {skills_str}\n"
            f"徽章: {badges_owned}/{badges_total}\n"
            f"心情: {session.mood}/100\n"
            f"{summary_section}\n"
            f"请用生动的小说风格叙述冒险，每次回复控制在100-200字。\n"
            f"如有事件发生，在叙事文本末尾用以下格式标注：\n"
            f"```events\n[JSON数组]\n```\n"
            f"可用事件类型:\n"
            f'- {{"type":"item","name":"物品名","rarity":"common/uncommon/rare/epic/legendary","description":"描述","effect":"HP+20"}}\n'
            f'- {{"type":"skill","name":"技能名","power":数值,"element":"fire/water/earth/wind/shadow/light","description":"描述"}}\n'
            f'- {{"type":"stat","stat":"HP/ATK/DEF/SPD/LCK","amount":数值}}\n'
            f'- {{"type":"tickets","amount":数值}}  (1-3张抽奖券)\n'
            f"\n注意：每次探索最多产生1-2个事件，不要过多。事件应与叙事内容相关。\n"
            f"NPC由系统管理，不要生成NPC事件。"
        )

    def _call_llm(self, user_msg: str, session: GameSession) -> str | None:
        """Call LLM, return raw response text or None on failure."""
        if not self._llm_available or not self._client:
            return None
        try:
            self._messages.append({"role": "user", "content": user_msg})
            response = self._client.create_message(
                model=self._MODEL,
                max_tokens=400,
                system=self._system_prompt(session),
                messages=self._messages[-self._MAX_HISTORY:],
            )
            text = ""
            for block in response.content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text += block.get("text", "")
                elif hasattr(block, "text"):
                    text += block.text
            text = text.strip()
            if text:
                self._messages.append({"role": "assistant", "content": text})
                self._maybe_summarize(session)
                return text
            return None
        except Exception:
            return None

    def _maybe_summarize(self, session: GameSession) -> None:
        """Summarize old messages if history is too long."""
        if len(self._messages) < self._SUMMARY_THRESHOLD:
            return
        # Take older messages for summary
        to_summarize = self._messages[:10]
        self._messages = self._messages[10:]

        if not self._llm_available or not self._client:
            # Offline: just concatenate truncated
            texts = [m["content"][:100] for m in to_summarize if m["role"] == "assistant"]
            self._summary += " ".join(texts)[:500]
            return

        try:
            summary_prompt = (
                "请将以下冒险记录摘要为200字以内的紧凑叙事，保留关键事件和发现：\n\n"
                + "\n".join(m["content"][:200] for m in to_summarize)
            )
            response = self._client.create_message(
                model=self._MODEL,
                max_tokens=300,
                messages=[{"role": "user", "content": summary_prompt}],
            )
            text = ""
            for block in response.content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text += block.get("text", "")
                elif hasattr(block, "text"):
                    text += block.text
            if text.strip():
                self._summary = text.strip()[:500]
        except Exception:
            texts = [m["content"][:100] for m in to_summarize if m["role"] == "assistant"]
            self._summary += " ".join(texts)[:500]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def narrate_arrival(self, session: GameSession) -> str:
        """Generate narrative for arriving at a new location."""
        loc = session.location
        if not loc:
            return f"{self._name}迷失了方向..."

        prompt = f"{self._name}来到了{loc.name}（{loc.region}）。请描述到达时的场景。"
        result = self._call_llm(prompt, session)
        if result:
            return self._extract_narrative(result)
        # Fallback
        return self._fallback_narrative(loc.region, session)

    def narrate_exploration(self, session: GameSession) -> tuple[str, list[dict]]:
        """Generate exploration narrative + events."""
        loc = session.location
        if not loc:
            return f"{self._name}四处张望...", []

        prompt = (
            f"{self._name}在{loc.name}进行了一次探索。"
            f"请描述探索过程并生成0-2个事件。"
        )
        result = self._call_llm(prompt, session)
        if result:
            narrative, events = self._parse_response(result)
            return narrative, events

        # Fallback: template + random event
        narrative = self._fallback_narrative(loc.region, session)
        events = self._fallback_events(loc, session)
        return narrative, events

    def narrate_npc_dialogue(self, npc: NPC, player_msg: str,
                              session: GameSession) -> str:
        """Generate NPC dialogue."""
        prompt = (
            f"{self._name}对{npc.name}（{npc.species}，性格：{npc.personality}，"
            f"态度：{npc.disposition}）说：'{player_msg}'。"
            f"请以{npc.name}的身份回复，符合其性格特点。"
        )
        result = self._call_llm(prompt, session)
        if result:
            return self._extract_narrative(result)
        # Fallback
        if npc.disposition == "friendly":
            return f"{npc.name}友好地微笑：\"很高兴见到你，{self._name}。\""
        elif npc.disposition == "hostile":
            return f"{npc.name}警惕地盯着{self._name}：\"你最好别惹麻烦。\""
        return f"{npc.name}点了点头：\"嗯。\""

    def narrate_rest(self, session: GameSession) -> str:
        """Generate rest narrative."""
        prompt = f"{self._name}在{session.location.name if session.location else '此处'}休息了一会儿。"
        result = self._call_llm(prompt, session)
        if result:
            return self._extract_narrative(result)
        return f"{self._name}找了一个舒适的角落坐下来休息，渐渐恢复了精力。"

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_response(self, text: str) -> tuple[str, list[dict]]:
        """Parse LLM response into narrative + events."""
        events: list[dict] = []
        narrative = text

        # Extract ```events ... ``` block
        pattern = r"```events?\s*\n(.*?)\n```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            narrative = text[:match.start()].strip()
            try:
                raw = match.group(1).strip()
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    events = [e for e in parsed if self._validate_event(e)]
                elif isinstance(parsed, dict):
                    if self._validate_event(parsed):
                        events = [parsed]
            except (json.JSONDecodeError, TypeError):
                pass

        return narrative, events[:2]  # cap at 2 events

    def _validate_event(self, event: dict) -> bool:
        """Validate event against expected schema."""
        if not isinstance(event, dict):
            return False
        etype = event.get("type")
        if etype == "item":
            return all(k in event for k in ("name", "rarity", "effect"))
        elif etype == "skill":
            return all(k in event for k in ("name", "power", "element"))
        elif etype == "stat":
            return "stat" in event and "amount" in event
        elif etype == "tickets":
            return "amount" in event
        return False

    def _extract_narrative(self, text: str) -> str:
        """Strip events block from response, return just narrative."""
        pattern = r"```events?\s*\n.*?\n```"
        return re.sub(pattern, "", text, flags=re.DOTALL).strip()

    # ------------------------------------------------------------------
    # Fallback
    # ------------------------------------------------------------------

    def _fallback_narrative(self, region: str, session: GameSession) -> str:
        templates = _TEMPLATES.get(region, _ALL_TEMPLATES)
        template = random.choice(templates)
        loc_name = session.location.name if session.location else "这里"
        return template.format(name=self._name, location=loc_name)

    def _fallback_events(self, loc: 'from .types import Location',
                          session: GameSession) -> list[dict]:
        """Generate random events based on location weights."""
        weights = loc.event_weights
        roll = random.random()
        cumulative = 0.0
        chosen = "nothing"
        for etype, prob in weights.items():
            cumulative += prob
            if roll < cumulative:
                chosen = etype
                break

        if chosen == "nothing":
            return []
        elif chosen == "item":
            item_data = random.choice(_FALLBACK_ITEMS)
            return [{"type": "item", **item_data}]
        elif chosen == "skill":
            skill_data = random.choice(_FALLBACK_SKILLS)
            return [{"type": "skill", **skill_data}]
        elif chosen == "stat":
            stat = random.choice(["HP", "ATK", "DEF", "SPD", "LCK"])
            amount = random.randint(1, 3)
            return [{"type": "stat", "stat": stat, "amount": amount}]
        elif chosen == "tickets":
            amount = random.randint(1, 3)
            lck = session.stats.get("LCK", 10)
            if lck > 30:
                amount += 1
            return [{"type": "tickets", "amount": amount}]
        return []
