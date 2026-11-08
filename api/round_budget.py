"""Bounded execution budgets from the existing, permission-normalized task plan."""


def select_round_budget(plan: dict, uploads: list[str], requested: int | None, *, fixed_workflow: bool = False) -> dict:
    if requested is not None and (type(requested) is not int or not 1 <= requested <= 6):
        raise ValueError('最多处理轮次必须是1～6的整数，或使用自动推荐')
    sources = set(plan.get('required_sources') or [])
    complexity = plan.get('complexity', 'standard')
    if fixed_workflow:
        recommended, reason = 3, '先执行只读业务契约，保留两次有界修正（引用定位、事实块与统计口径）'
    elif plan.get('require_pdf'):
        recommended, reason = 5, '任务包含PDF产物，需要预留内容和文件交付校验'
    elif complexity == 'complex':
        recommended = 5 if len(sources) > 1 or len(uploads) > 1 else 4
        reason = '任务计划判断存在多步依赖或复杂比较，需要预留有界纠错'
    elif plan.get('require_report') or plan.get('task_type') == 'report':
        recommended, reason = 4, '任务需要完整报告及内容结构校验'
    elif len(sources) > 1 or len(uploads) > 1:
        recommended, reason = 4, '任务涉及多个来源或附件，需要交叉核对'
    elif plan.get('task_type') == 'analysis':
        recommended, reason = 3, '单一来源分析任务，预留取数与结论修订'
    else:
        recommended, reason = 2, '直接问答或单一来源查询，保留一次修订机会'
    initial = min(recommended, 3) if requested is None else requested
    adaptive = requested is None
    return {'mode': 'auto' if requested is None else 'manual',
            'max_rounds': initial, 'initial_rounds': initial,
            'adaptive': adaptive, 'hard_limit': 6 if adaptive else initial,
            'recommended_rounds': recommended, 'reason': reason,
            'policy_version': 2}


def extend_round_budget(budget: dict, *, made_progress: bool, stalled: bool,
                        recoverable: bool) -> dict | None:
    """Grant one additional round only; manual limits and failed gates stay intact."""
    if (not budget.get('adaptive') or not made_progress or stalled or not recoverable
            or budget['max_rounds'] >= min(budget.get('hard_limit', 6), 6)):
        return None
    return {**budget, 'max_rounds': budget['max_rounds'] + 1,
            'reason': '仍有可修正项，且已补充实际证据、解决历史问题或减少校验错误；追加一轮，最高六轮'}
