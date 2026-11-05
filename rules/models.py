from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator

class RuleFilter(BaseModel):
    model_config = ConfigDict(extra='forbid',allow_inf_nan=False)
    field: str = Field(min_length=1, max_length=100)
    op: Literal['eq', 'ne', 'gt', 'gte', 'lt', 'lte', 'like', 'in'] = 'eq'
    value: str | int | float | bool | list[str | int | float | bool]

    @field_validator('value')
    @classmethod
    def bounded_values(cls, value):
        values=value if isinstance(value,list) else [value]
        if len(values)>50 or any(isinstance(v,str) and len(v)>500 for v in values):
            raise ValueError('默认过滤最多50个值，每个文本值不超过500字符')
        return value

class FormulaNode(BaseModel):
    model_config = ConfigDict(extra='forbid',allow_inf_nan=False)
    op: Literal['field','number','add','sub','mul','div','related_sum']
    field: str | None = Field(default=None,max_length=100)
    value: float | None = None
    args: list['FormulaNode'] = Field(default_factory=list,max_length=2)
    filters: list[RuleFilter] = Field(default_factory=list,max_length=8)

class MetricBinding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    metric_key: str = Field(min_length=1, max_length=100)
    aggregation: Literal['sum', 'avg', 'min', 'max', 'count', 'count_distinct'] | None = None
    field: str | None = Field(default=None, max_length=100)
    filters: list[RuleFilter] = Field(default_factory=list, max_length=8)
    formula: FormulaNode | None = None
    formula_mode: Literal['row','aggregate'] = 'row'
    formula_aggregation: Literal['sum','avg','min','max'] = 'sum'
    time_field: str | None = Field(default=None,max_length=100)

    @model_validator(mode='after')
    def expression_pair(self):
        if self.formula and (self.aggregation or self.field):
            raise ValueError('公式与单字段聚合只能选择一种')
        if bool(self.aggregation) != bool(self.field):
            raise ValueError('调整计算方式时必须同时选择聚合方式和字段')
        return self

class RuleDraft(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=80)
    category: Literal['business', 'term', 'preference']
    subject: str = Field(min_length=1, max_length=80, description='规则约束的同一概念，如销售额、回答结构')
    content: str = Field(min_length=1, max_length=1200)
    match_mode: Literal['keywords', 'all_tasks'] = 'keywords'
    aliases: list[str] = Field(default_factory=list,max_length=12)
    match_terms: list[str] = Field(default_factory=list, max_length=8)
    scope_note: str = Field(min_length=1, max_length=240)

    @field_validator('title', 'subject', 'content', 'scope_note')
    @classmethod
    def clean_text(cls, value):
        value = value.strip()
        if not value:
            raise ValueError('内容不能为空')
        return value

    @model_validator(mode='after')
    def scope_mode(self):
        if self.match_mode == 'all_tasks' and self.category != 'preference':
            raise ValueError('只有回答偏好允许适用于所有任务')
        if self.match_mode == 'keywords' and not self.match_terms:
            raise ValueError('请至少填写一个适用关键词')
        return self

    @field_validator('match_terms','aliases')
    @classmethod
    def clean_terms(cls, values):
        result = list(dict.fromkeys(v.strip() for v in values))
        if any(len(v) < 2 or len(v) > 60 for v in result):
            raise ValueError('每个适用关键词需为2～60个字符')
        return result

class RuleInput(RuleDraft):
    binding: MetricBinding | None = None
    active: bool = True

    @model_validator(mode='after')
    def binding_for_business(self):
        if self.category == 'business' and not self.binding:
            raise ValueError('业务口径必须绑定已有语义指标及可执行定义')
        if self.category != 'business' and self.binding:
            raise ValueError('只有业务口径可以绑定指标')
        return self
