"""World map — 6 regions, ~22 locations, fixed NPCs, world lore.

Each location defines connections, event weights, ticket bonuses,
and optionally fixed NPCs and monsters.
No LLM involved here; this is the fixed game framework.
"""
from __future__ import annotations

from .types import Location, Monster, NPC, Item

# ===========================================================================
# WORLD LORE
# ===========================================================================

WORLD_LORE = """
很久以前，六块大陆悬浮在虚空之中，由一棵名为"源树"的巨大世界树连接。
源树的根系贯穿每一片土地，它的生命力维系着整个世界的平衡。

然而，在一次被称为"大断裂"的灾难中，源树的核心被不明力量击碎，
碎片化为32枚徽章散落在六块大陆的各个角落。

没有了源树的维系，大陆之间的通道开始崩塌，怪物从虚空的裂隙中涌出，
曾经和平的居民们被迫各自求生。

传说，只要有人能收集齐全部32枚徽章，就能重新唤醒源树，
恢复世界的秩序。但至今，还没有冒险者能完成这项壮举。

你的伙伴，作为一个小小的冒险者，踏上了这条充满未知的旅途......
"""

# World secrets — NPC们会随机透露这些信息
WORLD_SECRETS: list[str] = [
    "源树的种子据说还埋在星光圣殿的王座之下...",
    "大断裂发生在整整一千年前的今天。",
    "幽暗森林里的古树是源树仅存的一根侧枝。",
    "水晶洞穴的晶体殿堂曾经是源树根系的结晶。",
    "风暴山脉的雷电其实是源树残留的能量在放电。",
    "深海遗迹原本是一座漂浮在空中的城市，大断裂后沉入了海底。",
    "机械废墟是古代文明试图用科技替代源树时建造的。",
    "星光圣殿的建造者至今不明，有人说是源树自己的意志。",
    "金色徽章据说是源树核心碎片中最纯净的两块。",
    "据说收集满16枚绿色徽章就能打开通往隐藏区域的路。",
    "大断裂之前，这个世界没有怪物。它们都是从虚空裂隙中来的。",
    "每一枚徽章都蕴含着源树的一丝力量，这就是它们有被动效果的原因。",
    "精灵泉的泉水是源树的眼泪凝聚而成。",
    "有人在深渊裂隙的最深处看到过源树根系的幻影。",
    "控制室里的古代投影记录了大断裂发生的全过程。",
    "命运之池能映照出你最终能收集到多少枚徽章...... 如果你敢看的话。",
    "堕落天使曾经是星光圣殿的守护者，大断裂让它失去了理智。",
    "有传闻说，某些NPC其实是源树创造的意志碎片。",
    "龙焰之心徽章据说是从一条远古巨龙身上取下的鳞片炼化而成。",
    "如果你仔细听，风暴山脉的风声中藏着源树的低语。",
]

# Buddy talk responses (without LLM, simple fixed responses)
BUDDY_TALK_RESPONSES: list[str] = [
    "{name}歪着头看着你，似乎在认真思考你说的话。",
    "{name}蹭了蹭你的手，发出了满足的声音。",
    "{name}点了点头，然后指向远方，似乎想继续冒险。",
    "{name}打了个哈欠，看起来有点困了。",
    "{name}兴奋地跳了跳，似乎很开心你和它说话！",
    "{name}眨了眨眼，然后对你露出了一个大大的微笑。",
    "{name}若有所思地看着天空，尾巴轻轻摇晃。",
    "{name}用爪子在地上画了一个奇怪的图案，然后得意地看着你。",
    "{name}发出了一串你听不太懂的声音，但你感觉它在讲今天的冒险。",
    "{name}靠在你身边，安静地享受这一刻。",
]

# ===========================================================================
# NPCs — fixed per location
# ===========================================================================

NPCS: dict[str, NPC] = {}

def _npc(name: str, species: str, personality: str, disposition: str,
         greeting: str, gifts: list[dict] | None = None,
         secrets: list[int] | None = None,
         gift_chance: float = 0.30, ignore_chance: float = 0.30,
         secret_chance: float = 0.40) -> NPC:
    # secrets are indices into WORLD_SECRETS
    secret_texts = [WORLD_SECRETS[i] for i in (secrets or []) if i < len(WORLD_SECRETS)]
    npc = NPC(
        name=name, species=species, personality=personality,
        disposition=disposition, greeting=greeting,
        gifts=gifts or [], secrets=secret_texts,
        gift_chance=gift_chance, ignore_chance=ignore_chance,
        secret_chance=secret_chance,
    )
    NPCS[name] = npc
    return npc

# -- 幽暗森林 NPCs --
_npc("老树精·莫斯", "树精", "慈祥但话少的森林守护者", "friendly",
     "莫斯缓缓睁开布满青苔的眼睛：\"年轻的旅人......\"",
     gifts=[{"name": "树精的祝福", "rarity": "uncommon", "effect": "HP+15", "description": "古老树精赐予的生命祝福"}],
     secrets=[2, 12], gift_chance=0.40, ignore_chance=0.20, secret_chance=0.40)

_npc("蘑菇商人·菌菇", "蘑菇人", "精明的蘑菇交易商", "friendly",
     "菌菇摇晃着圆圆的脑袋：\"今天有好货哦！\"",
     gifts=[
         {"name": "活力蘑菇", "rarity": "common", "effect": "HP+10", "description": "闻起来有点奇怪但很有营养"},
         {"name": "幸运蘑菇", "rarity": "uncommon", "effect": "LCK+2", "description": "四叶草形状的稀有蘑菇"},
     ],
     secrets=[0, 2], gift_chance=0.50, ignore_chance=0.20, secret_chance=0.30)

_npc("精灵·露娜", "精灵", "守护精灵泉的沉静精灵", "friendly",
     "露娜的身影在泉水的光辉中若隐若现：\"难得有访客......\"",
     gifts=[{"name": "精灵之泪", "rarity": "rare", "effect": "全属性+1", "description": "精灵泉中最珍贵的一滴泪"}],
     secrets=[12, 0], gift_chance=0.25, ignore_chance=0.25, secret_chance=0.50)

# -- 水晶洞穴 NPCs --
_npc("矮人矿工·铜锤", "矮人", "粗犷豪爽的老矿工", "friendly",
     "铜锤用锤子敲了敲水晶：\"嘿！又来找矿石的？\"",
     gifts=[
         {"name": "精炼铁矿", "rarity": "common", "effect": "DEF+2", "description": "矮人工艺打磨的铁矿"},
         {"name": "紫水晶", "rarity": "uncommon", "effect": "ATK+2", "description": "蕴含能量的紫色水晶"},
     ],
     secrets=[3], gift_chance=0.40, ignore_chance=0.30, secret_chance=0.30)

_npc("洞穴学者·石墨", "人类", "痴迷于研究水晶的学者", "neutral",
     "石墨头也不抬：\"别打扰我，我在分析这块晶体的频率......\"",
     gifts=[{"name": "研究笔记", "rarity": "uncommon", "effect": "LCK+3", "description": "记录着洞穴秘密的笔记"}],
     secrets=[3, 11], gift_chance=0.20, ignore_chance=0.50, secret_chance=0.30)

# -- 风暴山脉 NPCs --
_npc("流浪剑客·岚", "人类", "在山脉中修行的沉默剑客", "neutral",
     "岚没有说话，只是微微颔首。",
     gifts=[{"name": "破风石", "rarity": "uncommon", "effect": "SPD+3", "description": "剑客分享的风属性矿石"}],
     secrets=[4, 19], gift_chance=0.25, ignore_chance=0.50, secret_chance=0.25)

_npc("雷鹰驯养师·索尔", "兽人", "与雷鹰共生的驯养大师", "friendly",
     "索尔从雷鹰背上跳下来：\"哈！今天风很好！\"",
     gifts=[
         {"name": "雷鹰羽毛", "rarity": "rare", "effect": "SPD+5", "description": "闪烁着电弧的稀有羽毛"},
     ],
     secrets=[4, 10], gift_chance=0.30, ignore_chance=0.20, secret_chance=0.50)

# -- 深海遗迹 NPCs --
_npc("人鱼歌者·珊瑚", "人鱼", "用歌声抚慰灵魂的歌者", "friendly",
     "珊瑚的歌声在水中回荡，她微笑着向你游来。",
     gifts=[{"name": "海之旋律", "rarity": "uncommon", "effect": "HP+20", "description": "凝固的人鱼之歌"}],
     secrets=[5, 13], gift_chance=0.35, ignore_chance=0.15, secret_chance=0.50)

_npc("幽灵船长·雷克", "亡灵", "沉船的前任船长", "neutral",
     "雷克的虚影在船舱中飘荡：\"又一个活人......有什么事？\"",
     gifts=[{"name": "船长的藏品", "rarity": "rare", "effect": "ATK+4", "description": "船长生前珍藏的宝物"}],
     secrets=[5, 10], gift_chance=0.20, ignore_chance=0.40, secret_chance=0.40)

# -- 机械废墟 NPCs --
_npc("修理工·齿轮", "机器人", "还在运转的古代服务型机器人", "friendly",
     "齿轮发出嗡嗡声：\"检测到生命体。需要维修服务吗？\"",
     gifts=[
         {"name": "纳米修复剂", "rarity": "uncommon", "effect": "HP+15", "description": "古代科技的修复药剂"},
         {"name": "能量芯片", "rarity": "rare", "effect": "ATK+5", "description": "高能量密度的古代芯片"},
     ],
     secrets=[6, 14], gift_chance=0.40, ignore_chance=0.20, secret_chance=0.40)

_npc("数据幽灵·Zero", "AI", "废墟数据网络中残存的人工智能", "neutral",
     "屏幕上闪烁出文字：\"HELLO_WORLD... 你好，外来者。\"",
     gifts=[{"name": "解密密钥", "rarity": "rare", "effect": "LCK+5", "description": "打开隐藏数据的密钥"}],
     secrets=[6, 14, 10], gift_chance=0.20, ignore_chance=0.30, secret_chance=0.50)

# -- 星光圣殿 NPCs --
_npc("星语者·奥瑞尔", "天使", "圣殿中最后的守望者", "friendly",
     "奥瑞尔的光翼微微颤动：\"旅人......你来得正是时候。\"",
     gifts=[{"name": "星光碎片", "rarity": "epic", "effect": "全属性+2", "description": "源树核心的微小碎片"}],
     secrets=[7, 8, 15], gift_chance=0.25, ignore_chance=0.15, secret_chance=0.60)

_npc("命运织工·诺恩", "未知", "命运之池旁的神秘存在", "neutral",
     "诺恩没有转身，只是轻声说：\"命运已经注意到你了。\"",
     gifts=[{"name": "命运丝线", "rarity": "epic", "effect": "LCK+8", "description": "从命运之池中抽出的丝线"}],
     secrets=[15, 17, 8], gift_chance=0.20, ignore_chance=0.30, secret_chance=0.50)

# ===========================================================================
# NPC bindings to locations
# ===========================================================================

LOCATION_NPCS: dict[str, list[str]] = {
    "古树之心":   ["老树精·莫斯"],
    "蘑菇洼地":   ["蘑菇商人·菌菇"],
    "精灵泉":     ["精灵·露娜"],
    "矿脉通道":   ["矮人矿工·铜锤"],
    "晶体殿堂":   ["洞穴学者·石墨"],
    "山脚营地":   ["流浪剑客·岚"],
    "云端平台":   ["雷鹰驯养师·索尔"],
    "珊瑚迷宫":   ["人鱼歌者·珊瑚"],
    "沉船残骸":   ["幽灵船长·雷克"],
    "废弃工厂":   ["修理工·齿轮"],
    "数据中心":   ["数据幽灵·Zero"],
    "星图室":     ["星语者·奥瑞尔"],
    "命运之池":   ["命运织工·诺恩"],
}


def get_location_npcs(location_name: str) -> list[NPC]:
    """Get the fixed NPCs at a location."""
    names = LOCATION_NPCS.get(location_name, [])
    return [NPCS[n] for n in names if n in NPCS]


# ===========================================================================
# LOCATIONS
# ===========================================================================

LOCATIONS: dict[str, Location] = {}


def _loc(name: str, region: str, desc: str, connections: list[str],
         event_weights: dict[str, float] | None = None,
         ticket_bonus: int = 0) -> Location:
    loc = Location(
        name=name, region=region, description=desc,
        connections=connections,
        event_weights=event_weights or {
            "item": 0.25, "skill": 0.15, "stat": 0.15,
            "tickets": 0.25, "nothing": 0.20,
        },
        ticket_bonus=ticket_bonus,
    )
    LOCATIONS[name] = loc
    return loc


# ========================== 1. 幽暗森林 ==========================

_loc("林间小径", "幽暗森林",
     "阳光透过层叠的树冠洒落，蕨类植物在两侧轻轻摇曳。空气中弥漫着泥土和青草的芬芳。",
     ["古树之心", "蘑菇洼地"],
     {"item": 0.30, "skill": 0.10, "stat": 0.10, "tickets": 0.30, "nothing": 0.20})

_loc("古树之心", "幽暗森林",
     "一棵巨大的古树矗立在森林中央，粗壮的根系如同龙爪般深入大地。树干上隐约浮现着神秘的符文。传说这是源树仅存的一根侧枝。",
     ["林间小径", "精灵泉", "蘑菇洼地"],
     {"item": 0.25, "skill": 0.20, "stat": 0.15, "tickets": 0.20, "nothing": 0.20})

_loc("蘑菇洼地", "幽暗森林",
     "五颜六色的巨型蘑菇构成了一片奇异的景观，有些甚至大到可以当作房屋。空气中飘着甜腻的孢子。",
     ["林间小径", "古树之心", "入口大厅"],
     {"item": 0.35, "skill": 0.10, "stat": 0.10, "tickets": 0.25, "nothing": 0.20})

_loc("精灵泉", "幽暗森林",
     "清澈的泉水从古树根部涌出，散发着淡淡的光辉。传说这是源树的眼泪凝聚而成。",
     ["古树之心"],
     {"item": 0.20, "skill": 0.15, "stat": 0.25, "tickets": 0.20, "nothing": 0.20})

# ========================== 2. 水晶洞穴 ==========================

_loc("入口大厅", "水晶洞穴",
     "洞口被闪烁的水晶簇环绕，内壁折射出七彩的光芒。凉爽的气流从深处吹来，带着矿物的气息。",
     ["蘑菇洼地", "矿脉通道", "地底湖"],
     {"item": 0.30, "skill": 0.10, "stat": 0.10, "tickets": 0.30, "nothing": 0.20})

_loc("矿脉通道", "水晶洞穴",
     "狭窄的通道两壁嵌满了各色矿石，有些闪着幽蓝色的光。偶尔能听到采矿精灵的叮当声。",
     ["入口大厅", "晶体殿堂"],
     {"item": 0.35, "skill": 0.10, "stat": 0.15, "tickets": 0.25, "nothing": 0.15},
     ticket_bonus=1)

_loc("地底湖", "水晶洞穴",
     "一片宁静的地下湖泊，湖面如镜，倒映着洞顶密密麻麻的发光水晶。据说湖底藏着古老的宝藏。",
     ["入口大厅", "晶体殿堂", "沉船残骸"],
     {"item": 0.25, "skill": 0.15, "stat": 0.20, "tickets": 0.20, "nothing": 0.20})

_loc("晶体殿堂", "水晶洞穴",
     "巨大的天然水晶柱支撑着穹顶，整个空间充满了神圣的光辉。这些晶体据说是源树根系的结晶。",
     ["矿脉通道", "地底湖"],
     {"item": 0.30, "skill": 0.20, "stat": 0.15, "tickets": 0.20, "nothing": 0.15},
     ticket_bonus=1)

# ========================== 3. 风暴山脉 ==========================

_loc("山脚营地", "风暴山脉",
     "山脚下的营地驻扎着各路冒险者，篝火噼啪作响。远处的山峰被闪电照亮，雷声隆隆。",
     ["精灵泉", "悬崖小径"],
     {"item": 0.20, "skill": 0.20, "stat": 0.15, "tickets": 0.25, "nothing": 0.20})

_loc("悬崖小径", "风暴山脉",
     "狭窄的山路盘旋而上，一侧是峭壁，另一侧是万丈深渊。狂风呼啸，每一步都需要极大的勇气。",
     ["山脚营地", "云端平台"],
     {"item": 0.15, "skill": 0.30, "stat": 0.20, "tickets": 0.15, "nothing": 0.20})

_loc("云端平台", "风暴山脉",
     "悬浮在云层之上的神秘平台，四周是翻涌的云海。这里能感受到纯粹的元素之力——那是源树残留的能量。",
     ["悬崖小径", "山顶神殿"],
     {"item": 0.15, "skill": 0.35, "stat": 0.20, "tickets": 0.15, "nothing": 0.15},
     ticket_bonus=1)

_loc("山顶神殿", "风暴山脉",
     "山巅的古老神殿已半毁于风暴，但残留的石柱依然散发着强大的气场。雷电在殿顶交织。",
     ["云端平台"],
     {"item": 0.20, "skill": 0.30, "stat": 0.20, "tickets": 0.15, "nothing": 0.15})

# ========================== 4. 深海遗迹 ==========================

_loc("沉船残骸", "深海遗迹",
     "一艘巨大的帆船静静沉睡在海底，船身布满了珊瑚和海藻。大断裂之前，这里是一座空中之城。",
     ["地底湖", "珊瑚迷宫"],
     {"item": 0.30, "skill": 0.10, "stat": 0.20, "tickets": 0.20, "nothing": 0.20})

_loc("珊瑚迷宫", "深海遗迹",
     "五彩斑斓的珊瑚构成了错综复杂的迷宫，发光的水母在通道间飘荡。每个转角都可能有惊喜。",
     ["沉船残骸", "海神祭坛"],
     {"item": 0.25, "skill": 0.15, "stat": 0.25, "tickets": 0.15, "nothing": 0.20})

_loc("海神祭坛", "深海遗迹",
     "海底深处的古老祭坛，周围环绕着发光的海神雕像。这里的水流似乎蕴含着治愈的力量。",
     ["珊瑚迷宫", "深渊裂隙"],
     {"item": 0.20, "skill": 0.15, "stat": 0.30, "tickets": 0.15, "nothing": 0.20})

_loc("深渊裂隙", "深海遗迹",
     "海底最深处的裂隙，黑暗中闪烁着未知的光芒。有人说在裂隙最深处看到过源树根系的幻影。",
     ["海神祭坛", "废弃工厂"],
     {"item": 0.25, "skill": 0.20, "stat": 0.25, "tickets": 0.15, "nothing": 0.15},
     ticket_bonus=1)

# ========================== 5. 机械废墟 ==========================

_loc("废弃工厂", "机械废墟",
     "锈迹斑斑的齿轮和管道组成了复杂的机械结构。这是古代文明试图用科技替代源树时建造的遗迹。",
     ["深渊裂隙", "数据中心"],
     {"item": 0.25, "skill": 0.15, "stat": 0.20, "tickets": 0.20, "nothing": 0.20})

_loc("数据中心", "机械废墟",
     "密密麻麻的屏幕闪烁着绿色的代码流，某些终端似乎还在运行古老的程序。",
     ["废弃工厂", "能量核心"],
     {"item": 0.20, "skill": 0.25, "stat": 0.20, "tickets": 0.15, "nothing": 0.20})

_loc("能量核心", "机械废墟",
     "巨大的能量反应堆仍在缓慢运转，散发着幽蓝色的辉光。这里的能量密度令人窒息。",
     ["数据中心", "控制室"],
     {"item": 0.20, "skill": 0.20, "stat": 0.25, "tickets": 0.20, "nothing": 0.15},
     ticket_bonus=1)

_loc("控制室", "机械废墟",
     "废墟的中枢，巨大的全息投影仍在播放着大断裂发生时的最后影像。操控台上散落着各种零件。",
     ["能量核心", "前厅"],
     {"item": 0.25, "skill": 0.25, "stat": 0.15, "tickets": 0.15, "nothing": 0.20})

# ========================== 6. 星光圣殿 ==========================

_loc("前厅", "星光圣殿",
     "宏伟的大厅被无数漂浮的光球照亮，地面是纯白的大理石，每一步都回响着清脆的回音。",
     ["控制室", "星图室", "命运之池"],
     {"item": 0.20, "skill": 0.15, "stat": 0.15, "tickets": 0.30, "nothing": 0.20},
     ticket_bonus=1)

_loc("星图室", "星光圣殿",
     "穹顶上投射着整个宇宙的星图，星座缓慢旋转。这里的空气中充满了智慧与远古的记忆。",
     ["前厅", "王座大厅"],
     {"item": 0.15, "skill": 0.20, "stat": 0.20, "tickets": 0.25, "nothing": 0.20},
     ticket_bonus=1)

_loc("命运之池", "星光圣殿",
     "一池星辉流转的液体，据说能映照出命运的碎片。向池中投入祈愿，或许会得到回应。",
     ["前厅", "王座大厅"],
     {"item": 0.20, "skill": 0.15, "stat": 0.25, "tickets": 0.20, "nothing": 0.20},
     ticket_bonus=2)

_loc("王座大厅", "星光圣殿",
     "圣殿的最终之地，一座由纯光构成的王座漂浮在大厅中央。传说源树的种子就埋在王座之下。",
     ["星图室", "命运之池"],
     {"item": 0.20, "skill": 0.20, "stat": 0.15, "tickets": 0.25, "nothing": 0.20},
     ticket_bonus=2)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

START_LOCATION = "林间小径"

REGIONS: dict[str, list[str]] = {}
for _name, _loc_obj in LOCATIONS.items():
    REGIONS.setdefault(_loc_obj.region, []).append(_name)

REGION_ORDER = ["幽暗森林", "水晶洞穴", "风暴山脉", "深海遗迹", "机械废墟", "星光圣殿"]


def get_location(name: str) -> Location | None:
    return LOCATIONS.get(name)


def get_connections(name: str) -> list[str]:
    loc = LOCATIONS.get(name)
    return loc.connections if loc else []


# ---------------------------------------------------------------------------
# Monster pools by region
# ---------------------------------------------------------------------------

MONSTERS_BY_REGION: dict[str, list[Monster]] = {
    "幽暗森林": [
        Monster("林间蘑菇怪", "植物", hp=20, atk=5, defense=3, spd=4, element="earth", level=1,
                description="一只会走路的毒蘑菇，看起来不太友善。"),
        Monster("暗影狼", "野兽", hp=30, atk=8, defense=4, spd=7, element="shadow", level=2,
                description="在暗处潜行的灰狼，眼睛闪着幽光。"),
        Monster("树精", "精灵", hp=25, atk=6, defense=8, spd=3, element="earth", level=2,
                description="古树上剥落的树皮化成的精怪。"),
        Monster("荆棘藤蔓", "植物", hp=35, atk=7, defense=6, spd=2, element="earth", level=3,
                description="带刺的藤蔓突然活了过来！"),
    ],
    "水晶洞穴": [
        Monster("矿石傀儡", "构造体", hp=40, atk=10, defense=12, spd=3, element="earth", level=3,
                description="由碎矿石拼凑而成的笨重生物。"),
        Monster("洞穴蝙蝠群", "野兽", hp=25, atk=9, defense=4, spd=10, element="shadow", level=3,
                description="一群尖啸的蝙蝠从洞顶俯冲而下。"),
        Monster("水晶蜥蜴", "龙", hp=35, atk=11, defense=8, spd=6, element="light", level=4,
                description="浑身覆盖水晶鳞片的蜥蜴，折射出炫目的光。"),
        Monster("地底蠕虫", "虫", hp=50, atk=12, defense=5, spd=4, element="earth", level=4,
                description="从岩缝中钻出的巨型蠕虫。"),
    ],
    "风暴山脉": [
        Monster("雷鹰幼崽", "飞禽", hp=30, atk=13, defense=5, spd=12, element="wind", level=4,
                description="翅膀间闪烁着电弧的雷鹰幼崽。"),
        Monster("岩石巨人", "构造体", hp=60, atk=14, defense=15, spd=2, element="earth", level=5,
                description="由山岩化成的巨大人形生物。"),
        Monster("风暴精灵", "元素", hp=35, atk=15, defense=6, spd=11, element="wind", level=5,
                description="由纯粹的风暴能量凝聚而成。"),
        Monster("云端巨蟒", "野兽", hp=55, atk=16, defense=10, spd=7, element="wind", level=6,
                description="盘踞在云端的白色巨蟒。"),
    ],
    "深海遗迹": [
        Monster("深海水母", "水生", hp=30, atk=12, defense=4, spd=8, element="water", level=4,
                description="触须带有麻痹毒素的巨型水母。"),
        Monster("珊瑚守卫", "构造体", hp=50, atk=14, defense=14, spd=4, element="water", level=5,
                description="由活珊瑚构成的古老守卫。"),
        Monster("幽灵海盗", "亡灵", hp=45, atk=16, defense=8, spd=9, element="shadow", level=6,
                description="沉船中游荡的海盗亡灵。"),
        Monster("深渊章鱼", "水生", hp=65, atk=18, defense=10, spd=6, element="water", level=7,
                description="来自深渊的巨型章鱼，触须无数。"),
    ],
    "机械废墟": [
        Monster("故障机器人", "机械", hp=45, atk=15, defense=12, spd=5, element="fire", level=5,
                description="电路短路不断冒火花的机器人。"),
        Monster("激光炮台", "机械", hp=35, atk=20, defense=8, spd=1, element="fire", level=6,
                description="还在运转的防御炮台。"),
        Monster("纳米虫群", "机械", hp=40, atk=16, defense=6, spd=13, element="shadow", level=6,
                description="由无数微型机械虫组成的集群。"),
        Monster("核心守护者", "机械", hp=70, atk=18, defense=16, spd=5, element="fire", level=7,
                description="守护能量核心的高级战斗单位。"),
    ],
    "星光圣殿": [
        Monster("光之仆从", "元素", hp=50, atk=17, defense=10, spd=10, element="light", level=6,
                description="由纯光构成的圣殿仆从。"),
        Monster("星辰魔像", "构造体", hp=70, atk=20, defense=18, spd=4, element="light", level=7,
                description="镶嵌着星辰碎片的古老魔像。"),
        Monster("命运织网者", "元素", hp=55, atk=22, defense=12, spd=11, element="shadow", level=8,
                description="操纵命运之线的神秘存在。"),
        Monster("堕落天使", "神族", hp=80, atk=25, defense=15, spd=9, element="light", level=9,
                description="曾经守护圣殿的天使，大断裂让它失去了理智。"),
    ],
}


def get_random_monster(region: str) -> Monster | None:
    """Get a random monster for the given region."""
    import random
    pool = MONSTERS_BY_REGION.get(region)
    if not pool:
        return None
    template = random.choice(pool)
    def _vary(val: int) -> int:
        delta = max(1, val // 10)
        return val + random.randint(-delta, delta)
    return Monster(
        name=template.name, species=template.species,
        hp=_vary(template.hp), atk=_vary(template.atk),
        defense=_vary(template.defense), spd=_vary(template.spd),
        element=template.element, level=template.level,
        description=template.description,
    )
