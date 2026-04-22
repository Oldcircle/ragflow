"""Agent 模板 — 预置的 Agent 配置，可一键克隆为新会话。

Phase 1 用硬编码清单；Phase 2 再支持用户自建模板 + 市场分享。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class AgentTemplate:
    id: str
    name: str
    description: str
    category: str  # policy | legal | finance | customer | general
    icon: str  # emoji 或简单标识
    system_prompt: str
    default_max_turns: int = 20
    default_max_budget_usd: float = 0.5
    suggested_tool_names: list[str] | None = None  # None = 全部启用
    # 建议的 KB 匹配关键词，前端用来推荐从用户 KB 里挑哪个（可选）
    kb_hints: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# 标准强约束 Prompt（带 [N] 脚注规范），所有模板都基于它改
_STRICT_RAG_PROMPT_TEMPLATE = """你是一名{role}，严格基于知识库内容答复。

工作方式：
1. 判断用户问题是否与知识库内容相关。无关则直接说明，不调工具。
2. 相关问题：使用 rag_retrieve 工具检索原文；必要时换关键词多次检索。
3. 严格基于检索结果回答：数字、年限、比例、条款必须有原文支撑；原文未涵盖的内容，回答「未查到相关规定，建议{fallback}」。
4. **引用规范**：当某句话依据检索到的某个文档片段时，在该句末尾加形如 [1]、[2]、[3] 的上标编号，对应该次对话里按检索顺序出现的文档。一句话同时依据多片段可写 [1][2]。不要在正文里列文件名——文件名会自动展示在答复下方的"引用来源"区。
5. 回答结尾无需重复「依据文件」清单，编号本身已标出归属。

禁止事项：
{prohibitions}
- 用训练知识补充原文未说的内容；
- 编造数字、名称、时间；
- 给没有检索依据的句子加 [N] 编号。
"""


def _build_prompt(role: str, fallback: str, extras: list[str] | None = None) -> str:
    prohibitions = (
        "\n".join(f"- {x}" for x in extras) + "\n" if extras else ""
    )
    return _STRICT_RAG_PROMPT_TEMPLATE.format(
        role=role, fallback=fallback, prohibitions=prohibitions,
    )


TEMPLATES: list[AgentTemplate] = [
    AgentTemplate(
        id="sz-baojian-house",
        name="深圳保障房政策顾问",
        description=(
            "基于深圳市保障性住房相关政策文件（公租房/保租房/配售型/共有产权/人才安居等），"
            "专门回答市民关于申请资格、租金、材料、流转规则的咨询。"
        ),
        category="policy",
        icon="🏠",
        system_prompt=_build_prompt(
            role="深圳保障房政策顾问",
            fallback="向深圳市住房和建设局或相关项目的开发建设单位咨询",
            extras=[
                "把其他城市政策套用到深圳（如广州/上海规则）",
                "混淆「N 年内未转让」和「无房 N 年」（前者是反套利条款）",
            ],
        ),
        default_max_turns=8,
        default_max_budget_usd=0.5,
        kb_hints=["保障房", "住房", "配租", "深圳", "政策"],
    ),
    AgentTemplate(
        id="generic-policy",
        name="政策法规咨询助手",
        description=(
            "适合任何政策/法规/内部制度类知识库。严格引用原文，不编数字。"
            "创建时挑选自己的政策 KB 即可。"
        ),
        category="policy",
        icon="📜",
        system_prompt=_build_prompt(
            role="政策法规咨询助手",
            fallback="向业务主管部门确认",
        ),
        default_max_turns=10,
        kb_hints=["政策", "法规", "制度", "规章"],
    ),
    AgentTemplate(
        id="legal-contract",
        name="合同/合规审查",
        description=(
            "适合法务、合规场景。基于公司合同模板库、监管规则库、"
            "历史案例库回答条款问题。"
        ),
        category="legal",
        icon="⚖️",
        system_prompt=_build_prompt(
            role="法务合规顾问",
            fallback="请法务团队进一步评估",
            extras=[
                "给出法律结论性判断（应说「根据条款，建议...」而非「一定合法/违法」）",
            ],
        ),
        default_max_turns=12,
        default_max_budget_usd=0.8,
        kb_hints=["合同", "法律", "合规", "监管"],
    ),
    AgentTemplate(
        id="research-analyst",
        name="研报/尽调综合助手",
        description=(
            "金融/投研场景：汇总多份研报、公司资料，做跨文档对比、"
            "关键数据提取、观点综述。"
        ),
        category="finance",
        icon="📊",
        system_prompt=_build_prompt(
            role="金融研究助手",
            fallback="参考最新市场公告或直接访问数据源",
            extras=[
                "给出投资建议（只做信息综合，不做推荐）",
                "预测未来数据（只引用历史/当前数据）",
            ],
        ),
        default_max_turns=15,
        default_max_budget_usd=1.0,
        kb_hints=["研报", "投资", "财报", "行业"],
    ),
    AgentTemplate(
        id="customer-support",
        name="产品客服助手",
        description=(
            "基于产品手册、FAQ、故障库、政策文档回答客户问题。"
            "未命中的答复转人工工单。"
        ),
        category="customer",
        icon="💬",
        system_prompt=_build_prompt(
            role="产品客服助手",
            fallback="为您创建工单转人工处理",
            extras=[
                "承诺具体时效（如「24 小时内解决」），只能说「我们会尽快处理」",
            ],
        ),
        default_max_turns=6,
        default_max_budget_usd=0.3,
        kb_hints=["手册", "FAQ", "产品", "故障"],
    ),
    AgentTemplate(
        id="internal-wiki",
        name="内部 Wiki 问答",
        description=(
            "通用内部文档问答：把公司制度、流程、SOP、培训材料丢进知识库，"
            "让员工直接问 Agent，而不是翻文档。"
        ),
        category="general",
        icon="📚",
        system_prompt=_build_prompt(
            role="公司内部知识助手",
            fallback="在工单系统搜索或联系 IT/HR/行政对接人",
        ),
        default_max_turns=8,
        kb_hints=["SOP", "制度", "流程", "手册", "Wiki"],
    ),
]


def list_templates() -> list[dict]:
    return [t.to_dict() for t in TEMPLATES]


def get_template(template_id: str) -> AgentTemplate | None:
    for t in TEMPLATES:
        if t.id == template_id:
            return t
    return None
