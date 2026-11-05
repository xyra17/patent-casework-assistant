<template>
  <section class="cw-panel glass-card" aria-label="案件工作台">
    <ModuleNavigation title="案件工作台" :target="sidebarTarget">
      <div v-for="(group,index) in [tabs.slice(0,5),tabs.slice(5)]" :key="index" class="nav-links" :class="{'nav-secondary':index}">
        <button v-for="item in group" :key="item.id" type="button" :class="{active:tab===item.id}" :aria-current="tab===item.id ? 'page' : undefined" @click="setTab(item.id)">
          <el-icon aria-hidden="true"><component :is="item.icon"/></el-icon><span class="nav-label">{{ item.label }}</span>
        </button>
      </div>
    </ModuleNavigation>
    <header class="cw-header">
      <div class="workspace-heading"><h1>案件工作台</h1><span class="heading-divider" aria-hidden="true">/</span><span class="current-module">{{ tabs.find(t=>t.id===tab)?.label }}</span></div>
      <div class="cw-context" aria-label="案件数据概览">
        <dl v-if="overview" class="workspace-metrics"><div><dt>已发布案件</dt><dd>{{ overview.case_count }}</dd></div><div><dt>当前在办</dt><dd>{{ overview.active_count }}</dd></div><div :class="{'metric-attention':overview.conflict_cases}"><dt>待核对</dt><dd>{{ overview.conflict_cases }}</dd></div><div><dt>未分案</dt><dd>{{ overview.unknown_owner }}</dd></div></dl>
        <div class="overview-update">
          <span v-if="overview?.coverage.roots.length" class="last-published">更新于 {{ latestScan }}</span>
          <el-button class="overview-refresh" text :loading="busy.overview" :disabled="busy.overview" :icon="Refresh" aria-label="刷新案件数据" title="刷新案件数据" @click="refresh" />
        </div>
      </div>
    </header>
    <el-alert v-if="failure" :title="failure" type="error" show-icon :closable="false" />
    <el-alert v-if="overview && !overview.coverage.complete_scan" title="接入范围尚不完整；请到共享盘同步查看失败项与发布状态。当前结果仅对已发布资料成立。" type="warning" :closable="false" />
    <div class="cw-content" :class="`module-${tab}`" role="region" :aria-label="tabs.find(t=>t.id===tab)?.label" v-loading="busy[tab]">
      <template v-if="tab==='search'">
        <form class="cw-filters search-form" @submit.prevent="runSearch">
          <div class="filter-field field-wide"><label for="cw-search-query">技术描述</label><el-input id="cw-search-query" v-model="searchQuery" placeholder="输入所编号或技术描述，例如：向量召回后重排" aria-label="相似案件描述" clearable/></div>
          <div class="filter-field"><label for="cw-search-domain">技术领域</label><el-select id="cw-search-domain" v-model="searchDomain" clearable placeholder="全部领域" aria-label="技术域"><el-option v-for="d in domains" :key="d" :label="d" :value="d" /></el-select></div>
          <div class="filter-field"><label for="cw-search-owner">代理师</label><el-select id="cw-search-owner" v-model="searchOwner" filterable clearable placeholder="全部代理师" aria-label="检索代理师"><el-option v-for="name in searchOwners" :key="name" :label="name" :value="name" /></el-select></div>
          <div class="filter-submit"><el-button type="primary" native-type="submit" :loading="busy.search" :disabled="!searchQuery.trim()"><el-icon aria-hidden="true"><Search /></el-icon>检索案件</el-button></div>
        </form>
        <div v-if="!searchResult && !busy.search" class="search-guidance" role="status"><el-icon class="guidance-icon" aria-hidden="true"><Search /></el-icon><h3>从技术描述找到可参考的案卷</h3><p>不必记住文件名。描述技术机制，查看相关案件及原文依据。</p><div class="search-examples"><span>试试搜索</span><button v-for="example in ['向量检索与重排','模型蒸馏','无线信道估计']" :key="example" type="button" @click="searchQuery=example;runSearch()">{{ example }}<el-icon aria-hidden="true"><ArrowRight /></el-icon></button></div></div>
        <ArchiveSearchResults v-if="searchResult" :result="searchResult" @open-case="openCase" @open-source="source.open($event)"/>
        <el-button v-if="searchResult?.results.length" @click="askAgent('只查内部知识库，不联网、不查数据库。查找与'+searchResult.query+'相关的历史案件'+(searchResult.filters?.owner?'，仅查代理师'+searchResult.filters.owner:'')+(searchResult.filters?.domain?'，技术领域限定为'+searchResult.filters.domain:'')+'，核对原文，说明可借鉴机制和不适用之处。不生成文件。')">交给智能体进一步核对</el-button>
      </template>
      <template v-if="tab==='cases'">
        <form class="cw-filters cases-filters" @submit.prevent="loadCases(0)"><div class="filter-field field-wide"><label for="cw-case-query">搜索案件</label><el-input id="cw-case-query" v-model="filters.query" placeholder="案号、客户或案件名称" clearable aria-label="案件关键词"/></div><div class="filter-field "><label for="cw-owner">代理师</label><el-select id="cw-owner" v-model="filters.owner" clearable placeholder="全部代理师" aria-label="代理师"><el-option v-for="p in overview?.people || []" :key="p.id" :label="p.name" :value="p.name"/></el-select></div><div class="filter-field "><label for="cw-domain">技术领域</label><el-select id="cw-domain" v-model="filters.domain" clearable placeholder="全部领域" aria-label="领域"><el-option v-for="d in domains" :key="d" :label="d" :value="d"/></el-select></div><div class="filter-field "><label for="cw-due-field">期限节点</label><el-select id="cw-due-field" v-model="filters.due_field" placeholder="期限节点" aria-label="期限节点"><el-option v-for="f in dateFields" :key="f[0]" :value="f[0]" :label="f[1]"/></el-select></div><div class="filter-field "><label for="cw-due-status">期限状态</label><el-select id="cw-due-status" v-model="filters.due_status" clearable placeholder="全部期限" aria-label="期限状态"><el-option label="已逾期（仍在办）" value="overdue"/><el-option label="未来14天内（仍在办）" value="next14"/><el-option label="日期未知或冲突" value="unknown"/></el-select></div><div class="filter-submit"><el-button type="primary" native-type="submit">筛选</el-button><el-button @click="resetCaseFilters">重置</el-button></div></form>
        <div class="section-count"><strong>{{ caseRows?.total ?? '—' }} 件案件</strong><span>点击案号查看详情与来源</span></div><el-table scrollbar-always-on border class="app-resizable-table quiet-table-borders" :data="caseRows?.rows || []" stripe max-height="560" empty-text="没有符合条件的案件"><el-table-column label="案件" width="210" fixed><template #default="{row}"><button class="text-action" @click="openCase(row.case_key)">{{ row.case_id }}</button><p class="cell-title case-ledger-title" :title="row.title">{{ row.title || '名称未知' }}</p></template></el-table-column><el-table-column prop="client_case_id" label="客户案号" min-width="128" show-overflow-tooltip/><el-table-column prop="owner_name" label="代理师" min-width="170" show-overflow-tooltip/><el-table-column prop="technical_domain" label="领域" min-width="110"/><el-table-column label="案件状态" width="120"><template #default="{row}">{{ row.stage || '未登记' }}<el-tag v-if="row.conflict_fields.length" type="warning" size="small">待核对</el-tag></template></el-table-column><el-table-column v-for="f in dateFields" :key="f[0]" :label="f[1]" min-width="148"><template #default="{row}">{{ row[f[0]] || '未核实' }}</template></el-table-column><el-table-column label="实际交局" min-width="148"><template #default="{row}">{{ row.filing_verified ? row.actual_filed_date : '未核实' }}</template></el-table-column></el-table>
        <div class="cw-pagination"><span>共 {{ caseRows?.total || 0 }} 件 · 基准日 {{ caseRows?.as_of }}</span><el-button :disabled="!caseRows?.offset" @click="loadCases(caseRows.offset-30)">上一页</el-button><el-button :disabled="!caseRows?.has_more" @click="loadCases(caseRows.offset+30)">下一页</el-button></div>
      </template>
      <template v-if="tab==='meetings'">
        <form class="cw-filters meeting-filters" @submit.prevent="loadMeetings(0)">
          <div class="filter-field field-wide"><label for="cw-meeting-client">客户</label><el-select id="cw-meeting-client" v-model="meetingClient" filterable allow-create default-first-option clearable placeholder="搜索或输入客户全称" aria-label="会议客户"><el-option v-for="name in meetingResult?.options?.clients || []" :key="name" :label="name" :value="name"/></el-select></div>
          <div class="filter-field field-wide"><label for="cw-meeting-ipr">参会 IPR</label><el-select id="cw-meeting-ipr" v-model="meetingIpr" filterable allow-create default-first-option clearable placeholder="搜索或输入姓名，精确匹配" aria-label="参会IPR"><el-option v-for="name in meetingResult?.options?.iprs || []" :key="name" :label="name" :value="name"/></el-select></div>
          <div class="filter-submit"><el-button type="primary" native-type="submit">查找会议</el-button><el-button @click="resetMeetingFilters">重置</el-button></div>
        </form>
        <el-alert v-if="meetingResult?.same_name_across_clients" title="当前结果包含不同客户的同名IPR，请选择客户后核对身份。" type="warning" :closable="false"/>
        <details v-if="meetingResult?.agent_summary?.length" class="meeting-colleagues">
          <summary class="colleague-heading">查看参会同事<span>{{ meetingResult.agent_summary.length }} 人</span></summary>
          <div class="colleague-list"><div v-for="person in meetingResult.agent_summary" :key="person.name" class="colleague-item"><strong>{{ person.name }}</strong><span>{{ person.meeting_count }} 场 · {{ person.case_count }} 个案件</span><span>最近 {{ person.latest_date || '日期未登记' }}</span></div></div>
        </details>
        <div class="section-count"><strong>{{ meetingResult?.total ?? '—' }} 场会议</strong><span>按会议日期排序 · 同一案件多次会议分别保留</span></div>
        <el-table scrollbar-always-on border class="app-resizable-table quiet-table-borders" :data="meetingResult?.rows || []" stripe max-height="540" empty-text="未找到实际参会记录，可清空筛选或核对客户与IPR姓名">
          <el-table-column prop="date" label="会议日期" width="112"><template #default="{row}">{{ row.date || '未登记' }}</template></el-table-column>
          <el-table-column prop="client" label="客户" min-width="160" show-overflow-tooltip/>
          <el-table-column label="参会 IPR" min-width="160" show-overflow-tooltip><template #default="{row}">{{ row.iprs.join('、') || '未登记' }}</template></el-table-column>
          <el-table-column label="参会代理师" min-width="170" show-overflow-tooltip><template #default="{row}">{{ row.agents.join('、') || '未登记' }}</template></el-table-column>
          <el-table-column label="对应案件" min-width="200"><template #default="{row}"><button class="text-action" @click="openCase(row.case_key)">{{ row.case_id }}</button><p class="cell-title meeting-case-title" :title="row.title">{{ row.title || '名称未登记' }}</p></template></el-table-column>
          <el-table-column label="修改意见 / 会议要点" min-width="220" show-overflow-tooltip><template #default="{row}">{{ row.changes || row.content || '未登记，请核对原文' }}</template></el-table-column>
          <el-table-column label="" width="95" fixed="right"><template #default="{row}"><el-button link type="primary" @click="selectedMeeting=row;meetingVisible=true">查看详情</el-button></template></el-table-column>
        </el-table>
        <div class="cw-pagination"><span>共 {{ meetingResult?.total || 0 }} 场 · {{ meetingResult?.total ? meetingOffset+1 : 0 }}–{{ Math.min(meetingOffset+(meetingResult?.rows.length||0),meetingResult?.total||0) }}</span><el-button :disabled="!meetingOffset" @click="loadMeetings(meetingOffset-20)">上一页</el-button><el-button :disabled="!meetingResult?.has_more" @click="loadMeetings(meetingOffset+20)">下一页</el-button></div>
        <p class="meeting-scope-note">仅依据已登记的实际参会记录；立案联系人不等于参会人，单次意见也不代表 IPR 的固定偏好。</p>
      </template>
      <template v-if="tab==='reports'">
        <div class="report-controls"><div class="report-presets"><span>快捷月份</span><button v-for="item in reportPresets" :key="item.id" type="button" :class="{active:periodSelected(item.id)}" :aria-pressed="periodSelected(item.id)" @click="choosePeriod(item.id)">{{ item.label }}</button><small>近3／6个月按完整月份统计</small></div>
        <form class="report-filter-grid" @submit.prevent="loadReport">
          <div class="filter-field report-period-field"><label for="cw-report-period">统计月份</label><el-date-picker id="cw-report-period" v-model="reportRange" type="monthrange" format="YYYY年M月" value-format="YYYY-MM" start-placeholder="起始月份" end-placeholder="结束月份" range-separator="至" :clearable="false" unlink-panels aria-label="统计月份" popper-class="casework-month-picker"/></div>
          <div class="filter-field"><label for="cw-report-group">统计维度</label><el-select id="cw-report-group" v-model="reportGroup" aria-label="报表分组"><el-option label="技术领域" value="technical_domain"/><el-option label="代理师" value="owner_name"/><el-option label="客户" value="client_alias"/></el-select></div>
          <div class="filter-submit"><el-button type="primary" native-type="submit" :loading="busy.reports">更新统计</el-button><el-button :disabled="!report || busy.reports" @click="downloadCsv">导出 CSV</el-button></div>
        </form>
        </div>
        <p v-if="reportDraftChanged" class="report-pending" role="status">筛选已调整，点击“更新统计”应用。下方仍显示上次统计结果。</p>
        <template v-if="report">
          <div class="report-result-heading"><h3>{{ monthLabel(reportParams.start_month) }}<template v-if="reportParams.start_month!==reportParams.end_month"> 至 {{ monthLabel(reportParams.end_month) }}</template></h3><span>共 {{ report.totals.length }} 个月 · 按{{ reportGroupLabel }}统计<span v-if="includesCurrentMonth"> · 本月尚未结束</span></span></div>
          <dl class="report-summary"><div><dt>实际收案</dt><dd>{{ reportTotals.received }}<small>件</small></dd></div><div><dt>已核实实际交局</dt><dd>{{ reportTotals.filed }}<small>件</small></dd></div><div class="report-summary-note"><dt>统计口径</dt><dd>两个日期独立计算<small>计划期限、文件更新时间不计入完成量</small></dd></div></dl>
          <div class="report-layout">
            <section class="report-chart"><div class="block-heading"><h3>月度趋势</h3><span>点击数量查看案件</span></div><div class="report-legend"><span class="received-dot"/>实际收案<span class="filed-dot"/>已核实实际交局</div><div class="trend"><div v-for="r in report.totals" :key="r.month" class="trend-row"><strong>{{ monthLabel(r.month) }}</strong><div class="trend-bars"><div v-for="metric in ['received','filed']" :key="metric" :class="['trend-metric','metric-'+metric]"><div class="trend-track"><span :class="['bar',metric]" :style="{width:barWidth(r[metric+'_count'])}"/></div><button type="button" :disabled="!r[metric+'_count']" :aria-label="monthLabel(r.month)+(metric==='received'?'实际收案':'实际交局')+r[metric+'_count']+'件，查看案件'" @click="drillMonth(r.month,metric)">{{ r[metric+'_count'] }}</button></div></div></div></div></section>
            <section class="report-breakdown"><div class="block-heading"><h3>{{ reportGroupLabel }}明细</h3><span>点击数量查看案件</span></div><el-table scrollbar-always-on border class="app-resizable-table quiet-table-borders" :data="report.rows" max-height="390" stripe empty-text="所选月份暂无可统计的案件"><el-table-column label="月份" min-width="115"><template #default="{row}">{{ monthLabel(row.month) }}</template></el-table-column><el-table-column prop="group" :label="reportGroupLabel" min-width="115" show-overflow-tooltip/><el-table-column label="实际收案" min-width="95"><template #default="{row}"><button class="text-action" :disabled="!row.received_count" @click="drill(row.received_case_keys,'实际收案 · '+monthLabel(row.month))">{{ row.received_count }} 件</button></template></el-table-column><el-table-column label="实际交局（已核实）" min-width="145"><template #default="{row}"><button class="text-action filed-count" :disabled="!row.filed_count" @click="drill(row.filed_case_keys,'实际交局 · '+monthLabel(row.month))">{{ row.filed_count }} 件</button></template></el-table-column></el-table></section>
          </div>
          <section class="report-quality"><div class="block-heading"><h3>待核对的数据</h3><span>当前接入的全部案件</span></div><div class="unknown-row"><el-button @click="drillRows(report.unknown_received,'收案日期未知／冲突（当前接入的全部案件）')">收案日期未知 <strong>{{ report.unknown_received.length }}</strong></el-button><el-button @click="drillRows(report.unknown_filed,'完成日期或核实依据缺失（当前接入的全部案件）')">交局未核实 <strong>{{ report.unknown_filed.length }}</strong></el-button><el-button v-if="report.future_filed.length" @click="drillRows(report.future_filed,'未来实际日期异常')">未来日期异常 <strong>{{ report.future_filed.length }}</strong></el-button></div><p class="muted">日期未知的案件无法归入所选月份，单独列出。{{ report.coverage.scope_note }}</p><el-alert v-if="report.duplicate_case_ids_excluded.length" type="warning" :closable="false" :title="'跨目录重复案号已排除：'+report.duplicate_case_ids_excluded.join('、')"/></section>
          <details class="report-topics"><summary>客户技术主题观察<span>{{ topics?.rows.length || 0 }} 条记录</span></summary><p class="muted">同一案件可以涉及多个主题，主题数量不能相加为案件总量，也不代表客户整体研发方向。</p><el-table scrollbar-always-on border class="app-resizable-table quiet-table-borders" :data="topics?.rows || []" max-height="340" empty-text="所选月份暂无已识别的技术主题"><el-table-column label="收案月份" min-width="115"><template #default="{row}">{{ monthLabel(row.month) }}</template></el-table-column><el-table-column prop="client" label="客户" min-width="150"/><el-table-column prop="tag" label="正文技术主题" min-width="160"/><el-table-column label="涉及案件" min-width="100"><template #default="{row}"><button class="text-action" @click="drill(row.case_keys,row.tag)">{{ row.case_count }} 件</button></template></el-table-column></el-table></details>
        </template>
      </template>
      <template v-if="tab==='assign'">
        <form class="recommend-form" @submit.prevent="recommend()"><div class="filter-field"><label for="cw-assign-query">新案件的技术描述</label><el-input id="cw-assign-query" v-model="assignQuery" type="textarea" :rows="3" placeholder="输入交底标题及关键技术机制，例如：通过蒸馏降低模型推理开销" aria-label="待分案技术描述"/></div><div class="cw-filters assign-filters"><div class="filter-field field-wide"><label for="cw-assign-case">待分案案号</label><el-input id="cw-assign-case" v-model="assignCase" placeholder="可留空，仅查看候选" aria-label="待分案案号"/></div><div class="filter-field"><label for="cw-assign-domain">新案领域（可选）</label><el-select id="cw-assign-domain" v-model="assignDomain" clearable placeholder="选择领域" aria-label="分案领域"><el-option v-for="d in domains" :key="d" :label="d" :value="d"/></el-select></div><div class="filter-submit"><el-button type="primary" native-type="submit" :loading="busy.assign" :disabled="!assignQuery.trim()">查看候选与负荷</el-button><el-button @click="peopleVisible=true">维护代理师</el-button></div></div></form>
        <p v-if="recommendationDraftChanged" class="report-pending" role="status">查询条件已调整，请重新查询。下方仍显示上次结果，暂不能据此确认分案。</p>
        <template v-if="recommendation">
          <section v-if="recommendation.requires_user_confirmation" class="recommendation-gap" aria-labelledby="recommendation-gap-title">
            <h3 id="recommendation-gap-title">还不能确定谁有相关经验</h3>
            <p>{{ recommendation.message }}</p>
            <p class="muted">查找内容：{{ recommendation.query }}</p>
            <p class="muted">已补查案件文档，找到 {{ recommendation.technical_lookup?.results.length || 0 }} 个可能相关的案件；相关材料不等于已确认的承办经历。</p>
            <div class="filter-submit"><el-button type="primary" @click="focusAssignmentQuery">补充技术描述</el-button><el-button :disabled="busy.assign || recommendationDraftChanged" @click="recommend('workload_only')">先看在办案件与期限</el-button></div>
            <details v-if="recommendation.technical_lookup?.results.length"><summary>查看找到的相关材料</summary><div v-for="hit in recommendation.technical_lookup.results" :key="hit.case_key" class="proof"><button class="text-action" @click="openCase(hit.case_key)">{{ hit.case_id }} · {{ hit.title }}</button><el-button v-for="proof in hit.sources" :key="proof.file_id+proof.location" link type="primary" @click="source.open(proof)">查看原文</el-button></div></details>
          </section>
          <template v-else>
            <details class="recommendation-method"><summary>{{ workloadOnly ? '人员负荷统计口径' : '推荐依据与统计口径' }} · {{ recommendation.as_of }}</summary><p class="muted">{{ recommendation.formula }}</p><p class="muted">{{ recommendation.deadline_definition }}</p></details>
            <p v-if="workloadOnly" class="muted" role="status">已按你的选择仅展示人员负荷，按姓名排列；本次不推荐接案人。</p>
            <el-empty v-if="!recommendation.candidates.length" description="暂无启用的代理师，请先维护代理师名单" :image-size="60"/>
            <article v-for="(r,i) in recommendation.candidates" :key="r.person_id" class="cw-result">
              <div class="result-head"><h3>{{ workloadOnly ? r.name : `${i+1}. ${r.name}` }}</h3><span>{{ r.bound_domains.join(' / ') }}</span><span v-if="!workloadOnly" class="muted">排序分 {{ r.score }}</span><el-button v-if="recommendation.case_key && !workloadOnly" :disabled="recommendationDraftChanged || busy.assign || busy.mutation" @click="assignTo(r)">确认分给此人</el-button></div>
              <dl class="candidate-stats"><div v-if="!workloadOnly"><dt>相关历史</dt><dd>{{ r.history_match_count }}<small>件</small></dd></div><div><dt>已知在办</dt><dd>{{ r.active_count }}<small>件</small></dd></div><div><dt>14天内到期</dt><dd>{{ r.near_due_cases.length }}<small>件</small></dd></div><div><dt>逾期</dt><dd>{{ r.overdue_cases.length }}<small>件</small></dd></div><div><dt>状态未知</dt><dd>{{ r.unknown_stage_cases.length }}<small>件</small></dd></div></dl>
              <p class="muted">{{ r.basis }} · 状态与日期缺失不代表空闲；经历标签不代表撰写质量。</p>
              <details v-if="!workloadOnly"><summary>查看相关历史与原文</summary><el-empty v-if="!r.history_matches.length" description="此人暂无已核对的相关经历，领域与负荷仅供参考" :image-size="40"/><div v-for="h in r.history_matches" :key="h.case_key" class="proof"><button class="text-action" @click="openCase(h.case_key)">{{ h.case_id }} · {{ h.title }}</button><p>{{ h.matched_tags.join('、') }}</p><el-button v-for="p in h.sources" :key="p.file_id+p.location" link type="primary" @click="source.open(p)">查看历史依据</el-button></div></details>
              <details><summary>查看在办、期限和缺失情况</summary><p v-for="c in [...r.active_cases,...r.unknown_stage_cases]" :key="c.case_key"><button class="text-action" @click="openCase(c.case_key)">{{ c.case_id }}</button> · {{ c.stage || '状态未知' }} · 初稿 {{ c.draft_due_date || '未知' }} · 送审 {{ c.client_review_due_date || '未知' }} · 交局 {{ c.filing_due_date || '未知' }}</p><p class="muted">在办案件缺失日期（逐节点）：{{ dateFields.map(([k,n])=>n+' '+(r.unknown_dates_by_node[k]||0)+' 件').join('；') }}</p></details>
            </article>
          </template>
        </template>
      </template>
      <template v-if="tab==='sync'">
        <div class="sync-section-heading"><div class="sync-directory-summary"><span class="sync-directory-count">已接入 <strong>{{ overview?.roots.length ?? 0 }}</strong> 个目录</span><span class="sync-directory-hint">展开目录信息可查看路径与维护操作</span></div><el-button type="primary" @click="editRoot()"><el-icon aria-hidden="true"><FolderOpened /></el-icon>接入目录</el-button></div>
        <el-empty v-if="overview && !overview.roots.length" description="尚未接入共享目录，请先接入服务端白名单中的目录" :image-size="60"/>
        <article v-for="r in overview?.roots || []" :key="r.id" class="sync-root" :class="{'sync-root-disabled':!r.enabled}">
          <div class="sync-root-heading"><div class="sync-root-name"><el-icon aria-hidden="true"><FolderOpened /></el-icon><h3>{{ r.label }}</h3><span class="sync-classification">{{ r.classification==='synthetic'?'模拟资料':'私密资料 · 仅限本地' }}</span></div><div class="sync-root-action"><el-tag :type="!r.enabled?'info':r.state==='ready'?'success':'warning'" effect="plain">{{ !r.enabled?'已停用':stateName(r.state) }}</el-tag><el-button :disabled="!r.enabled || r.state==='scanning'" :loading="r.state==='scanning'" @click="scan(r)">立即同步</el-button></div></div>
          <dl class="sync-root-meta"><div><dt>最近扫描</dt><dd>{{ formatTime(r.last_scan) }}</dd></div><div><dt>业务数据同步</dt><dd :class="{'sync-state-ready':r.mirror_state==='ready','sync-state-error':['error','interrupted'].includes(r.mirror_state)}">{{ stateName(r.mirror_state) }}</dd></div><div><dt>发布版本</dt><dd>{{ r.generation || '尚未发布' }}<span v-if="r.mirror_generation != null" class="sync-version-note"> · 业务数据 {{ r.mirror_generation }}</span></dd></div></dl>
          <p v-if="r.last_error" class="sync-root-error" role="alert">{{ r.last_error }}</p>
          <div v-if="r.state==='needs_confirmation'" class="sync-pending"><span>发现原文件消失，请先核对挂载与文件清单。</span><el-button type="warning" size="small" @click="scan(r,true)">核对并清理消失文件索引</el-button></div>
          <details class="sync-maintenance"><summary>目录信息与维护</summary><div class="sync-directory-path"><span>服务端路径</span><p>{{ r.path }}</p></div><div class="sync-maintenance-actions"><el-button size="small" @click="editRoot(r)">配置与停用</el-button><el-button size="small" @click="retryMirror(r)">重试业务数据同步</el-button><el-button v-if="!r.enabled" size="small" type="danger" plain @click="purge(r)">移除应用索引</el-button></div></details>
        </article>
        <div class="sync-history-heading"><h3>最近同步记录</h3><el-button v-if="(overview?.jobs.length || 0)>5" link type="primary" @click="showAllJobs=!showAllJobs">{{ showAllJobs ? '仅看最近5次' : '查看最近30次' }}</el-button></div>
        <el-table scrollbar-always-on border :data="visibleJobs" row-key="id" class="app-resizable-table quiet-table-borders sync-history-table" empty-text="暂无同步记录" max-height="420">
          <el-table-column type="expand" width="36" :resizable="false" label-class-name="no-column-resize"><template #default="{row:j}"><div class="sync-job-detail"><p>{{ phaseName(j.progress.phase) }}<span v-if="j.summary.generation"> · 发布版本 {{ j.summary.generation }}</span></p><p v-if="j.summary.embedding" class="muted">{{ j.summary.embedding }}</p><dl class="sync-job-counts"><div><dt>解析</dt><dd>{{ j.summary.parsed ?? '—' }}</dd></div><div><dt>未变</dt><dd>{{ j.summary.unchanged ?? '—' }}</dd></div><div><dt>失败</dt><dd>{{ j.summary.failed ?? '—' }}</dd></div><div><dt>移出索引</dt><dd>{{ j.summary.removed ?? '—' }}</dd></div><div><dt>疑似移动</dt><dd>{{ j.summary.renamed ?? '—' }}</dd></div></dl><div v-if="j.summary.issues?.length"><h4>待核对项（最多200条）</h4><p v-for="(v,i) in j.summary.issues" :key="i" class="wrap">{{ v.path }} · {{ v.reason }}</p></div><div v-if="j.summary.pending_removals?.length"><h4>待清理文件 {{ j.summary.pending_removal_count }} 个（最多列200个）</h4><p v-for="v in j.summary.pending_removals" :key="v" class="wrap">{{ v }}</p></div></div></template></el-table-column>
          <el-table-column label="开始时间" min-width="175"><template #default="{row}">{{ formatTime(row.started_at) }}</template></el-table-column>
          <el-table-column label="状态" min-width="100"><template #default="{row}"><el-tag :type="row.state==='ready'?'success':['error','interrupted'].includes(row.state)?'danger':'warning'" size="small" effect="plain">{{ stateName(row.state) }}</el-tag></template></el-table-column>
          <el-table-column label="已处理 / 发现文件" min-width="140"><template #default="{row}">{{ row.progress.processed || 0 }} / {{ row.progress.discovered || 0 }}</template></el-table-column>
          <el-table-column label="解析 / 未变" min-width="120"><template #default="{row}">{{ row.summary.parsed ?? '—' }} / {{ row.summary.unchanged ?? '—' }}</template></el-table-column>
          <el-table-column label="失败 / 待清理" min-width="120"><template #default="{row}"><span :class="{'sync-count-warning':row.summary.failed || row.summary.pending_removal_count}">{{ row.summary.failed ?? '—' }} / {{ row.summary.pending_removal_count ?? '—' }}</span></template></el-table-column>
        </el-table>
        <details class="sync-deployment"><summary>接入与保密说明</summary><p class="muted">真实部署需在服务端配置允许接入的目录与本地模型。接入私密目录后，云模型及联网检索会被阻断；停用目录不会自动解除限制，以保护已有任务与证据。</p></details>
      </template>
      <template v-if="tab==='audit'">
        <div class="audit-toolbar"><span>共 <strong>{{ auditResult?.total ?? '—' }}</strong> 条记录<span class="audit-sort"> · 最新操作在前</span></span><div class="audit-filter"><label for="cw-audit-type">操作类型</label><el-select id="cw-audit-type" v-model="auditType" clearable placeholder="全部操作" aria-label="操作类型" @change="loadAudit(0)"><el-option v-for="item in AUDIT_ACTIONS" :key="item.value" :label="item.label" :value="item.value"/></el-select></div></div>
        <el-table scrollbar-always-on border class="app-resizable-table quiet-table-borders" :data="auditResult?.rows || []" row-key="id" stripe max-height="min(420px, 42vh)" empty-text="暂无符合条件的操作记录">
          <el-table-column label="时间" width="175"><template #default="{row}">{{ formatTime(row.created_at) }}</template></el-table-column>
          <el-table-column label="操作" min-width="140"><template #default="{row}"><span class="audit-action-name">{{ auditAction(row.action) }}</span></template></el-table-column>
          <el-table-column label="操作对象" min-width="220" show-overflow-tooltip><template #default="{row}">{{ auditObject(row,overview?.roots,overview?.people) }}</template></el-table-column>
          <el-table-column label="变更摘要" min-width="270" show-overflow-tooltip><template #default="{row}">{{ auditSummary(row,overview?.field_definitions) }}</template></el-table-column>
          <el-table-column label="" width="95" fixed="right"><template #default="{row}"><el-button link type="primary" @click="selectedAudit=row;auditVisible=true">查看详情</el-button></template></el-table-column>
        </el-table>
        <div class="cw-pagination"><span>每页30条 · {{ auditResult?.total ? auditOffset+1 : 0 }}–{{ Math.min(auditOffset+(auditResult?.rows.length||0),auditResult?.total||0) }} / {{ auditResult?.total || 0 }}</span><el-button :disabled="!auditOffset" @click="loadAudit(auditOffset-30)">上一页</el-button><el-button :disabled="!auditResult?.has_more" @click="loadAudit(auditOffset+30)">下一页</el-button></div>
        <p class="audit-actor-note">操作人沿用历史记录中的填写值，不代表已验证的登录身份。</p>
      </template>
    </div>
    <el-drawer v-model="auditVisible" title="操作详情" size="min(740px, 96vw)" append-to-body>
      <div v-if="selectedAudit" class="audit-detail">
        <h2>{{ auditAction(selectedAudit.action) }}</h2><p class="audit-detail-summary">{{ auditSummary(selectedAudit,overview?.field_definitions) }}</p>
        <dl class="audit-detail-meta"><div><dt>操作时间</dt><dd>{{ formatTime(selectedAudit.created_at) }}</dd></div><div><dt>操作人（记录值）</dt><dd>{{ selectedAudit.actor || '未登记' }}</dd></div><div><dt>操作对象</dt><dd>{{ auditObject(selectedAudit,overview?.roots,overview?.people) }}</dd></div><div><dt>操作依据</dt><dd>{{ selectedAudit.reason || '原记录未填写' }}</dd></div></dl>
        <section v-if="auditChanges(selectedAudit,overview?.field_definitions).length" class="audit-changes"><h3>记录前后对照</h3><p class="audit-change-note">仅列出本次记录的字段；未出现的字段不表示已删除。</p><div class="audit-diff-heading"><span>字段</span><span>操作前</span><span>操作后</span></div><div v-for="change in auditChanges(selectedAudit,overview?.field_definitions)" :key="change.key" class="audit-diff-row"><strong>{{ change.label }}</strong><span>{{ change.beforeRecorded ? change.before : '原记录未包含' }}</span><span>{{ change.after }}</span></div></section>
        <p v-else class="audit-change-note">{{ selectedAudit.action==='clear_confirmation' ? '本次撤销人工确认；原确认内容保留在下方原始记录中，不表示案件字段被清空。' : '本次操作未保存字段对照，可展开原始记录核对。' }}</p>
        <details class="audit-raw"><summary>查看原始记录</summary><pre>{{ JSON.stringify({id:selectedAudit.id,action:selectedAudit.action,target:selectedAudit.target,before:selectedAudit.before,after:selectedAudit.after},null,2) }}</pre></details>
      </div>
    </el-drawer>
    <el-drawer v-model="meetingVisible" title="三方会议详情" size="min(720px, 96vw)" append-to-body>
      <div v-if="selectedMeeting" class="meeting-detail">
        <div class="meeting-detail-heading"><span>{{ selectedMeeting.date || '会议日期未登记' }}</span><h2>{{ selectedMeeting.title || selectedMeeting.case_id }}</h2><button class="text-action" @click="openCase(selectedMeeting.case_key)">{{ selectedMeeting.case_id }} · 查看案件</button></div>
        <dl class="meeting-participants"><div><dt>客户</dt><dd>{{ selectedMeeting.client || '未登记' }}</dd></div><div><dt>参会 IPR</dt><dd>{{ selectedMeeting.iprs.join('、') || '未登记' }}</dd></div><div><dt>参会代理师</dt><dd>{{ selectedMeeting.agents.join('、') || '未登记' }}</dd></div><div><dt>发明人</dt><dd>{{ selectedMeeting.inventors.join('、') || '未登记' }}</dd></div></dl>
        <section v-for="key in ['content','pending','changes']" :key="key" class="meeting-detail-section"><h3>{{ meetingFields[key] }}</h3><p>{{ selectedMeeting[key] || '原记录未登记此项' }}</p></section>
        <details v-if="Object.keys(meetingFields).some(key=>!['content','pending','changes'].includes(key)&&selectedMeeting[key])" class="meeting-technical"><summary>查看技术讨论与发明点</summary><dl class="meeting-details"><template v-for="(label,key) in meetingFields" :key="key"><template v-if="!['content','pending','changes'].includes(key)&&selectedMeeting[key]"><dt>{{ label }}</dt><dd>{{ selectedMeeting[key] }}</dd></template></template></dl></details>
        <div class="meeting-originals"><h3>来源原文</h3><button v-for="(proof,index) in selectedMeeting.sources" :key="proof.file_id+proof.location+index" type="button" class="meeting-source-link" @click="source.open(proof)"><span>{{ proof.path?.split('/').pop() || '会议记录原件' }}</span><small>{{ proof.location }}</small><span class="source-link-action">查看原文 →</span></button></div>
      </div>
    </el-drawer>
    <el-drawer v-model="caseVisible" title="案件详情与字段依据" size="min(950px, 96vw)" append-to-body>
      <div v-if="busy.detail && !detail" class="cw-detail-loading" v-loading="busy.detail" aria-label="正在加载案件详情"></div>
      <div v-loading="busy.detail" v-if="detail" class="cw-detail"><h2>{{ detail.case.case_id }} · {{ detail.case.title }}</h2><p class="muted">{{ detail.case.root_id }} · 发布版本 {{ detail.case.generation }}。人工确认保存在平台；原件变化后需重新核对。</p><el-tabs><el-tab-pane label="案件信息与字段核对"><article v-for="[key,f] in orderedFields" :key="key" class="field-row"><div><strong>{{ f.label }}</strong><span :class="['field-state',f.state]">{{ fieldState(f.state) }}</span><p>{{ f.value ?? '未登记或来源冲突' }}</p></div><el-button v-if="key!=='case_id'" link type="primary" @click="startConfirm(key,f)">核对字段</el-button><details v-if="f.candidates.length || f.confirmation"><summary>查看来源与确认依据</summary><p v-for="(c,i) in f.candidates" :key="i">{{ c.value }} · <button class="text-action" @click="source.open(c)">{{ c.path }} · {{ c.location }}</button></p><p v-if="f.confirmation">人工确认 {{ f.confirmation.value }} · {{ f.confirmation.actor }} · {{ f.confirmation.reason }}</p><el-button v-if="f.confirmation" link type="danger" @click="clearField(key)">撤销人工确认</el-button></details></article></el-tab-pane><el-tab-pane label="案卷与历史版本"><article v-for="d in detail.documents" :key="d.id" class="field-row"><div><strong>{{ d.relative_path }}</strong><p class="muted">{{ d.kind==='drawing' ? '绘图文件' : d.kind }} · {{ d.active ? stateName(d.state):'已移出当前索引' }} · {{ d.revision?.slice(0,12) }}</p><p v-if="d.error">{{ d.error }}</p></div><el-button @click="source.open({file_id:d.id})">{{ d.state==='inventory' ? '查看附件' : '查看解析与版本' }}</el-button></article></el-tab-pane><el-tab-pane label="技术经历标签"><article v-for="tag in detail.case.tags" :key="tag.tag" class="field-row"><strong>{{ tag.tag }}</strong><p class="muted">根据案卷中的技术关键词匹配，仅供查找相关经历参考。</p><el-button v-for="p in tag.sources" :key="p.file_id+p.location" link type="primary" @click="source.open(p)">查看依据 · {{ p.location }}</el-button></article></el-tab-pane></el-tabs></div>
    </el-drawer>
    <el-dialog v-model="confirmVisible" title="人工核对字段" width="min(560px,94vw)" append-to-body><el-form label-position="top"><el-form-item :label="overview?.field_definitions[confirmForm.field]"><el-select v-if="confirmForm.field==='filing_confirmed'" v-model="confirmForm.value"><el-option label="已核实" :value="true"/><el-option label="未核实" :value="false"/></el-select><el-date-picker v-else-if="confirmForm.field.endsWith('_date')" v-model="confirmForm.value" value-format="YYYY-MM-DD" type="date"/><el-input v-else v-model="confirmForm.value"/></el-form-item><el-form-item label="依据文件（实际交局必须选）"><el-select v-model="confirmForm.source_id" clearable><el-option v-for="d in detail?.documents.filter(d=>d.active&&d.state==='parsed') || []" :key="d.id" :value="d.id" :label="d.relative_path"/></el-select></el-form-item><el-form-item label="核对理由与原文位置"><el-input v-model="confirmForm.reason" type="textarea" :rows="3"/></el-form-item><el-form-item label="操作人（非认证身份）"><el-input v-model="confirmForm.actor"/></el-form-item></el-form><template #footer><el-button @click="confirmVisible=false">取消</el-button><el-button type="primary" :loading="busy.mutation" @click="confirmField">保存核对记录</el-button></template></el-dialog>
    <SharedDirectoryDialog ref="rootEditor" @saved="refresh"/>
    <el-dialog v-model="peopleVisible" title="代理师与绑定领域" width="min(700px,94vw)" append-to-body><p class="muted">按姓名关联历史记录；领域用于无历史证据时兜底，不能直接推断技术能力。</p><div v-for="p in peopleDraft" :key="p.id" class="person-row"><strong>{{ p.name }}</strong><el-select v-model="p.domains" multiple filterable allow-create default-first-option :aria-label="p.name+'绑定领域'"><el-option v-for="d in domains" :key="d" :label="d" :value="d"/></el-select><el-switch v-model="p.enabled" active-text="启用"/><el-button @click="savePerson(p)">保存</el-button></div><h3>新增代理师</h3><div class="cw-filters"><el-input v-model="newPerson.name" placeholder="展示姓名"/><el-select v-model="newPerson.domains" multiple filterable allow-create default-first-option placeholder="选择绑定领域" aria-label="新增代理师绑定领域"><el-option v-for="d in domains" :key="d" :label="d" :value="d"/></el-select><el-button @click="savePerson(newPerson)">添加</el-button></div></el-dialog>
    <el-dialog v-model="drillVisible" :title="drillTitle" width="min(850px,96vw)" append-to-body><el-table scrollbar-always-on border class="app-resizable-table quiet-table-borders" :data="drillData" max-height="480"><el-table-column label="所编号" min-width="160"><template #default="{row}"><button class="text-action" @click="openCase(row.case_key)">{{ row.case_id }}</button></template></el-table-column><el-table-column prop="title" label="发明名称" min-width="200"/><el-table-column prop="owner_name" label="代理师" min-width="170" show-overflow-tooltip/><el-table-column prop="received_date" label="实际收案" min-width="120"/><el-table-column prop="actual_filed_date" label="实际交局" min-width="120"/></el-table></el-dialog>
    <CaseDocumentDrawer ref="source"/>
  </section>
</template>
<script setup>
import {ref,reactive,computed,onMounted,onBeforeUnmount,watch} from 'vue'
import {ElMessage,ElMessageBox} from 'element-plus'
import {Refresh,Search,ArrowRight,FolderOpened,Briefcase,ChatLineSquare,DataAnalysis,User,Clock} from '@element-plus/icons-vue'
import {casework,casePath} from '../caseworkApi'
import {AUDIT_ACTIONS,auditAction,auditObject,auditSummary,auditChanges} from '../auditPresentation'
import ModuleNavigation from './ModuleNavigation.vue'
import CaseDocumentDrawer from './CaseDocumentDrawer.vue'
import ArchiveSearchResults from './ArchiveSearchResults.vue'
import SharedDirectoryDialog from './SharedDirectoryDialog.vue'
import {caseFieldLabel} from '../sourcePresentation.js'
import {reportPeriod,monthLabel,monthRangeError} from '../reportPeriod'
const props=defineProps({sidebarTarget:{type:String,default:''}})
const emit=defineEmits(['ask'])
const tabs=[
  {id:'cases',icon:Briefcase,label:'案件信息',description:'三种期限分开查看；空值、冲突不会自动推断。点击案号查看字段依据和人工核对记录。'},
  {id:'search',icon:Search,label:'相似案件',description:'检索交底、申请和 Visio 图中文字；也可输入所编号查关联绘图。历史描述仅供参考，引用前核对原文。'},
  {id:'meetings',icon:ChatLineSquare,label:'IPR会议',description:'查找与同一位 IPR 沟通过的代理师，参考对应案件中的会议内容和修改意见。'},
  {id:'reports',icon:DataAnalysis,label:'月度统计',description:'分别按实际收案日期、已核实的实际交局日期统计，可按领域、代理师或客户查看趋势与明细。'},
  {id:'assign',icon:User,label:'辅助分案',description:'结合有据技术经历和当前在办负荷推荐候选，供 Boss 决策；人工确认后才保存平台分案记录。'},
  {id:'sync',icon:FolderOpened,label:'共享盘同步',description:'管理接入目录与更新状态。原件只读，资料变化在成功同步后生效。'},
  {id:'audit',icon:Clock,label:'操作记录',description:'核对目录同步、案件字段与分案的历史变更，可按操作类型筛选并查看详情。'},
]
const tab=ref('cases'),overview=ref(null),busy=reactive({}),failure=ref(''),source=ref(null)
const domains=computed(()=>overview.value?.domains||[]),dateFields=[['draft_due_date','初稿期限／所限'],['client_review_due_date','客户送审期限'],['filing_due_date','向局提交期限']]
const meetingFields={content:'会议内容',pending:'待补材料',changes:'修改意见',existing_art:'现有技术及缺陷',closest_art:'最接近现有技术',distinguishing_features:'区别技术特征及问题',comparison:'对比分析',primary_points:'主要发明点',secondary_points:'次要发明点'}
const latestScan=computed(()=>formatTime(overview.value?.coverage.roots.map(r=>r.last_scan).filter(Boolean).sort().at(-1)))
const showAllJobs=ref(false)
const visibleJobs=computed(()=>(overview.value?.jobs||[]).slice(0,showAllJobs.value?30:5))
const requests=new Map();let timer,disposed=false
async function action(key,fn){if(disposed)return;const serial=(requests.get(key)||0)+1;requests.set(key,serial);busy[key]=true;failure.value='';try{return await fn(()=>requests.get(key)===serial)}catch(e){if(requests.get(key)===serial&&e!=='cancel'&&e!=='close'){failure.value=e.message||String(e);ElMessage.error(failure.value)}}finally{if(requests.get(key)===serial)busy[key]=false}}
async function refresh(){await action('overview',async current=>{const r=await casework('/overview');if(current())overview.value=r});if(tab.value==='cases')await loadCases(caseRows.value?.offset||0);if(tab.value==='meetings')await loadMeetings(meetingOffset.value);if(tab.value==='reports')await loadReport();if(tab.value==='audit')await loadAudit(auditOffset.value);schedule()}
function schedule(){clearTimeout(timer);if(!disposed&&overview.value?.roots.some(r=>r.state==='scanning'))timer=setTimeout(refresh,2000)}
function setTab(value){tab.value=value;if(value==='cases')loadCases(0);if(value==='meetings')loadMeetings(0);if(value==='reports'&&!report.value)loadReport();if(value==='audit')loadAudit(0)}
const searchQuery=ref(''),searchDomain=ref(''),searchOwner=ref(''),searchResult=ref(null)
const searchOwners=computed(()=>[...new Set([...(overview.value?.owners || []),...(overview.value?.people || []).map(p=>p.name)])].sort())
function runSearch(){const params={query:searchQuery.value,domain:searchDomain.value,owner:searchOwner.value,limit:30};return action('search',async current=>{const r=await casework('/search',{params});if(current())searchResult.value=r})}
const filters=reactive({query:'',owner:'',domain:'',due_field:'draft_due_date',due_status:''}),caseRows=ref(null)
function resetCaseFilters(){Object.assign(filters,{query:'',owner:'',domain:'',due_field:'draft_due_date',due_status:''});loadCases(0)}
function loadCases(offset=0){const params={...filters,offset,limit:30};return action('cases',async current=>{const r=await casework('/cases',{params});if(current())caseRows.value=r})}
const meetingClient=ref(''),meetingIpr=ref(''),meetingResult=ref(null),meetingOffset=ref(0),selectedMeeting=ref(null),meetingVisible=ref(false)
function resetMeetingFilters(){meetingClient.value='';meetingIpr.value='';loadMeetings(0)}
function loadMeetings(offset=0){const params={client:meetingClient.value,ipr:meetingIpr.value,offset,limit:20};return action('meetings',async current=>{const r=await casework('/meetings',{params});if(current()){meetingResult.value=r;meetingOffset.value=offset}})}
const reportRange=ref(reportPeriod('last3')),reportGroup=ref('technical_domain'),report=ref(null),topics=ref(null),reportParams=ref(null)
const reportPresets=[{id:'last',label:'上月'},{id:'last3',label:'近3个月'},{id:'last6',label:'近6个月'},{id:'year',label:'今年'}]
const reportGroupLabel=computed(()=>({technical_domain:'技术领域',owner_name:'代理师',client_alias:'客户'}[reportParams.value?.group_by]||'技术领域'))
const reportTotals=computed(()=>(report.value?.totals||[]).reduce((totals,row)=>({received:totals.received+row.received_count,filed:totals.filed+row.filed_count}),{received:0,filed:0}))
const reportDraftChanged=computed(()=>reportParams.value&&(reportParams.value.start_month!==reportRange.value?.[0]||reportParams.value.end_month!==reportRange.value?.[1]||reportParams.value.group_by!==reportGroup.value))
const includesCurrentMonth=computed(()=>{const month=reportPeriod('year')[1];return reportParams.value?.start_month<=month&&reportParams.value?.end_month>=month})
function periodSelected(id){const value=reportPeriod(id);return reportRange.value?.[0]===value[0]&&reportRange.value?.[1]===value[1]}
function choosePeriod(id){reportRange.value=reportPeriod(id);loadReport()}
function loadReport(){return action('reports',async current=>{const error=monthRangeError(reportRange.value);if(error)throw new Error(error);const params={start_month:reportRange.value[0],end_month:reportRange.value[1],group_by:reportGroup.value};const [r,t]=await Promise.all([casework('/reports/monthly',{params}),casework('/reports/topics',{params:{start_month:params.start_month,end_month:params.end_month}})]);if(current()){report.value=r;topics.value=t;reportParams.value=params}})}
function barWidth(n){const max=Math.max(1,...report.value.totals.flatMap(r=>[r.received_count,r.filed_count]));return n/max*100+'%'}
function drillMonth(month,metric){const keys=report.value.rows.filter(row=>row.month===month).flatMap(row=>row[metric+'_case_keys']);drill(keys,(metric==='received'?'实际收案':'实际交局')+' · '+monthLabel(month))}
async function downloadCsv(){const r=await fetch('/api/casework/reports/monthly.csv?'+new URLSearchParams(reportParams.value));if(!r.ok)return ElMessage.error('下载失败，请重试');const u=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=u;a.download='案件月报.csv';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000)}
const drillVisible=ref(false),drillTitle=ref(''),drillData=ref([])
function drillRows(rows,title){drillData.value=rows;drillTitle.value=title;drillVisible.value=true}
async function drill(keys,title){return action('reports',async()=>{const known=new Map((report.value?.cases||[]).map(c=>[c.case_key,c]));const rows=await Promise.all(keys.map(async k=>known.get(k)||(await casework(casePath(k))).case));drillRows(rows,title)})}
const detail=ref(null),caseVisible=ref(false),confirmVisible=ref(false),confirmForm=reactive({field:'',value:'',source_id:'',reason:'',actor:'本机操作'})
function openCase(key){return action('detail',async current=>{caseVisible.value=true;detail.value=null;const r=await casework(casePath(key));if(current())detail.value=r})}
function startConfirm(field,f){Object.assign(confirmForm,{field,value:f.value??'',source_id:'',reason:'',actor:'本机操作'});confirmVisible.value=true}
function confirmField(){return action('mutation',async()=>{await casework(casePath(detail.value.case.case_key)+'/confirm',{method:'POST',body:{changes:{[confirmForm.field]:confirmForm.value===''?null:confirmForm.value},expected_version:detail.value.case.version,source_id:confirmForm.source_id||null,reason:confirmForm.reason,actor:confirmForm.actor}});confirmVisible.value=false;await openCase(detail.value.case.case_key);await refresh();ElMessage.success('核对记录已保存，原件未改写')})}
function clearField(field){return action('mutation',async()=>{const r=await ElMessageBox.prompt('说明撤销依据，保存后恢复来源候选值。','撤销人工确认',{inputValidator:v=>!!v?.trim()||'请填写依据'});await casework(casePath(detail.value.case.case_key)+'/clear-confirmation',{method:'POST',body:{field,expected_version:detail.value.case.version,reason:r.value}});await openCase(detail.value.case.case_key);await refresh()})}
const assignQuery=ref(''),assignDomain=ref(''),assignCase=ref(''),recommendation=ref(null),recommendationParams=ref(null)
const assignmentInput=()=>({query:assignQuery.value.trim(),domain:assignDomain.value||null,case_id:assignCase.value.trim().toUpperCase()})
const recommendationDraftChanged=computed(()=>recommendationParams.value&&JSON.stringify(assignmentInput())!==JSON.stringify(recommendationParams.value))
const workloadOnly=computed(()=>recommendation.value?.recommendation_mode==='workload_only')
function focusAssignmentQuery(){document.getElementById('cw-assign-query')?.focus()}
function recommend(mode='technical'){const params=assignmentInput();return action('assign',async current=>{let key=null;if(params.case_id){const page=await casework('/cases',{params:{query:params.case_id,limit:200}});const exact=page.rows.filter(c=>c.case_id===params.case_id);if(exact.length!==1)throw new Error('待分案编号未找到或有跨目录重复，请先在案件信息中核对');key=exact[0].case_key}const r=await casework('/recommendations',{method:'POST',body:{query:params.query,domain:params.domain,case_key:key,recommendation_mode:mode}});if(current()){recommendation.value=r;recommendationParams.value=params}})}
function assignTo(person){if(recommendationDraftChanged.value||workloadOnly.value||busy.assign||busy.mutation)return;const key=recommendation.value?.case_key;if(!key)return;return action('mutation',async()=>{const current=await casework(casePath(key));const answer=await ElMessageBox.prompt(`案件 ${current.case.case_id}：${current.case.owner_name||'未分案'} → ${person.name}。只保存平台记录，请填写分案依据。`,'确认分案',{inputValidator:v=>!!v?.trim()||'请填写依据',confirmButtonText:'确认保存分案'});await casework(casePath(key)+'/assign',{method:'POST',body:{person_id:person.person_id,expected_version:current.case.version,reason:answer.value,confirmed:true}});ElMessage.success('分案已留痕，请按所内流程维护源表');await refresh();await recommend()})}
const rootEditor=ref(null)
function editRoot(root){rootEditor.value.open(root,{allowedRoots:overview.value?.allowed_roots||[],fields:overview.value?.field_definitions||{}})}
function scan(root,confirm=false){return action('sync',async()=>{if(confirm)await ElMessageBox.confirm('已核对待清理清单与挂载状态？继续将移除消失文件的当前索引，保留历史解析和共享盘原件。','确认清理索引',{type:'warning'});await casework('/roots/'+root.id+'/scan',{method:'POST',body:{confirm_missing:confirm}});await refresh()})}
function retryMirror(root){return action('sync',async()=>{const r=await casework('/roots/'+root.id+'/mirror',{method:'POST'});await refresh();if(r.state==='error')throw new Error('业务数据同步失败，请查看同步状态并检查数据库连接');ElMessage.success('业务数据同步状态：'+stateName(r.state))})}
function purge(root){return action('mutation',async()=>{await ElMessageBox.confirm('移除该目录在应用中的索引、解析历史和字段覆盖记录。共享盘原件保留，私密资料外发锁仍保留。','移除应用索引',{type:'warning',confirmButtonText:'确认移除索引'});await casework('/roots/'+root.id,{method:'DELETE',params:{expected_version:root.version,confirmed:true}});await refresh()})}
const peopleVisible=ref(false),peopleDraft=ref([]),newPerson=reactive({name:'',domains:[],enabled:true})
watch(()=>overview.value?.people,rows=>{peopleDraft.value=(rows||[]).map(p=>({...p,enabled:!!p.enabled,domains:[...p.domains]}))})
function savePerson(p){return action('mutation',async()=>{await casework('/people'+(p.id?'/'+p.id:''),{method:p.id?'PUT':'POST',body:{name:p.name,domains:p.domains,enabled:!!p.enabled,notes:p.notes||'',expected_version:p.version||null}});recommendation.value=null;recommendationParams.value=null;await refresh();if(!p.id)Object.assign(newPerson,{name:'',domains:[],enabled:true});ElMessage.success('人员配置已保存')})}
const auditResult=ref(null),auditOffset=ref(0),auditType=ref(''),selectedAudit=ref(null),auditVisible=ref(false)
function loadAudit(offset=0){return action('audit',async current=>{const r=await casework('/audit',{params:{offset,limit:30,action:auditType.value}});if(current()){auditResult.value=r;auditOffset.value=offset}})}
function formatTime(v){return v?new Date(v).toLocaleString('zh-CN',{hour12:false}):'尚未发布'}
function phaseName(v){return {parse:'解析文件',publish:'发布当前索引',finished:'处理完成'}[v]||'准备同步'}
function stateName(v){return {ready:'已同步',new:'待首次扫描',scanning:'同步中',partial:'部分完成',needs_rescan:'需重新扫描',needs_confirmation:'待确认消失文件',unavailable:'目录不可用',disabled:'已停用',pending:'等待同步到业务数据库',not_published:'尚未发布',parsed:'解析成功',inventory:'仅登记文件',error:'失败',removed:'已移除',interrupted:'已中断',superseded:'已被更新版本替代'}[v]||v}
const fieldOrder=['case_id','title','client_alias','client_case_id','owner_name','technical_domain','stage','received_date','actual_filed_date','filing_confirmed','draft_due_date','client_review_due_date','filing_due_date','ipr_contact','application_type','commission_type','meeting_flag','important','urgent','review_exemption','notes']
const orderedFields=computed(()=>Object.entries(detail.value?.case.fields||{}).map(([key,field])=>[key,{...field,label:caseFieldLabel(field.label)}]).sort(([a],[b])=>(fieldOrder.indexOf(a)<0?999:fieldOrder.indexOf(a))-(fieldOrder.indexOf(b)<0?999:fieldOrder.indexOf(b))))
function fieldState(v){return {verified_source:'来源一致',unknown:'未核实',conflict:'来源冲突',confirmed:'人工已确认',confirmation_stale:'确认依据已变化'}[v]||v}
function askAgent(query){emit('ask',query)}
onMounted(refresh);onBeforeUnmount(()=>{disposed=true;clearTimeout(timer);requests.clear()})
</script>
<style scoped>
.cw-panel {height:100%;min-width:0;min-height:0;overflow:auto;box-sizing:border-box;padding:24px;container-type:inline-size;color:var(--text);font-size:14px;}
.recommendation-gap {margin-top:18px;padding:18px;border:1px solid var(--el-color-warning-light-7);border-radius:8px;background:var(--el-color-warning-light-9);line-height:1.75;}
.recommendation-gap h3 {font-size:16px;margin:0 0 10px;}
.recommendation-gap p {overflow-wrap:anywhere;}
.recommendation-gap details {margin-top:16px;}
.cw-header {display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:14px 24px;margin-bottom:20px;padding-bottom:18px;border-bottom:1px solid var(--border);}
.workspace-heading {min-width:0;display:flex;align-items:baseline;gap:12px;}
.workspace-heading h1 {font-size:20px;line-height:1.4;margin:0;font-weight:600;white-space:nowrap;}
.heading-divider {color:var(--text-secondary);}
.current-module {font-size:15px;color:var(--text-secondary);}
.filter-submit :deep(.el-icon),.module-toolbar :deep(.el-icon) {margin-right:5px;}
.cw-context {display:flex;align-items:center;flex-wrap:wrap;gap:8px 20px;min-width:0;margin-left:auto;}
.workspace-metrics {display:flex;flex-wrap:wrap;gap:10px 18px;margin:0;}
.workspace-metrics > div {display:flex;align-items:baseline;gap:7px;}
.workspace-metrics dt {font-size:12px;color:var(--text-secondary);white-space:nowrap;}
.workspace-metrics dd {margin:0;font-size:16px;font-weight:600;font-variant-numeric:tabular-nums;}
.workspace-metrics .metric-attention dd {color:var(--warning);}
.last-published {font-size:12px;color:var(--text-secondary);font-variant-numeric:tabular-nums;}
.overview-update {display:flex;align-items:center;gap:5px;}
.overview-refresh.el-button {width:28px;height:28px;padding:0;color:var(--text-secondary);border-radius:6px;}
.overview-refresh.el-button:hover {color:var(--primary);background:var(--primary-soft);}
.text-action:focus-visible,.search-examples button:focus-visible {outline:2px solid var(--primary);outline-offset:-2px;}
.cw-content {min-height:280px;padding:4px 0 0;}
.cw-filters {display:grid;grid-template-columns:repeat(3,minmax(0,1fr)) auto;align-items:end;gap:12px;margin:0 0 20px;padding:16px;background:var(--bg);border:1px solid var(--border);border-radius:8px;}
.filter-field {min-width:0;display:flex;flex-direction:column;gap:7px;}
.filter-field > label {color:var(--text);font-size:13px;line-height:1.5;font-weight:500;}
.filter-field :deep(.el-input),.filter-field :deep(.el-select),.filter-field :deep(.el-date-editor) {width:100%;min-width:0;box-sizing:border-box;}
.filter-submit {display:flex;align-items:center;justify-content:flex-end;gap:8px;min-height:32px;}
.filter-submit :deep(.el-button + .el-button) {margin-left:0;}
.search-form {grid-template-columns:minmax(220px,1fr) 145px 155px auto;}
.cases-filters {grid-template-columns:minmax(180px,1.6fr) repeat(4,minmax(120px,1fr)) auto;gap:10px;}
.meeting-filters {grid-template-columns:minmax(160px,1fr) minmax(160px,1fr) auto;}
.assign-filters {grid-template-columns:minmax(200px,1fr) 180px auto;border:none;padding:0;background:none;margin:16px 0 0;}
.section-count,.module-toolbar {display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;font-size:13px;margin:0 0 12px;}
.section-count strong {font-size:13px;font-weight:500;}
.section-count span,.module-toolbar > span {font-size:12px;color:var(--text-secondary);}
.search-guidance {display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;min-height:220px;padding:20px 16px;}
.guidance-icon {font-size:27px;color:var(--chart-muted);margin-bottom:12px;}
.search-guidance h3 {font-size:16px;line-height:1.5;font-weight:500;margin:0 0 7px;}
.search-guidance p {font-size:13px;color:var(--text-secondary);line-height:1.7;margin:0;}
.search-examples {display:flex;align-items:center;justify-content:center;flex-wrap:wrap;gap:8px 14px;margin-top:22px;font-size:12px;color:var(--text-secondary);}
.search-examples button {display:flex;align-items:center;gap:5px;border:1px solid var(--border);background:var(--card-bg);color:var(--text);font:inherit;padding:6px 10px;border-radius:6px;cursor:pointer;}
.search-examples button:hover {color:var(--primary);border-color:var(--primary);}
.cw-result {border:1px solid var(--border);border-radius:9px;padding:18px;margin:14px 0;background:var(--card-bg);}
.result-head {display:flex;align-items:center;flex-wrap:wrap;gap:8px 12px;}
.result-head > div:first-child {flex:1 1 250px;}
.result-head h3 {font-size:16px;line-height:1.5;margin:0;}
.case-ledger-title {white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.result-head p,.cell-title {margin:4px 0 0;color:var(--text-secondary);font-size:13px;line-height:1.6;}
.result-head > .text-action:last-child,.result-head > .el-button:last-child {margin-left:auto;}
.text-action {background:none;border:0;padding:0;color:var(--primary);font:inherit;text-align:left;cursor:pointer;line-height:1.65;overflow-wrap:anywhere;}
.text-action:hover {text-decoration:underline;}
.proof {border-top:1px solid var(--border);margin-top:14px;padding-top:12px;line-height:1.75;}
.proof p {white-space:pre-wrap;margin:0 0 8px;}
.proof small {display:block;color:var(--text-secondary);overflow-wrap:anywhere;font-size:12px;margin-top:5px;}
.muted {color:var(--text-secondary);font-size:13px;line-height:1.75;overflow-wrap:anywhere;}
.cw-pagination {display:flex;gap:8px;align-items:center;justify-content:flex-end;flex-wrap:wrap;margin-top:14px;padding-top:12px;border-top:1px solid var(--border);}
.cw-pagination span {margin-right:auto;color:var(--text-secondary);font-size:12px;}
.cw-pagination :deep(.el-button + .el-button) {margin-left:0;}
.meeting-participants {display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;padding:14px 0;margin:12px 0 0;border-top:1px solid var(--border);}
.meeting-participants dt {color:var(--text-secondary);font-size:12px;margin-bottom:4px;}
.meeting-participants dd {margin:0;line-height:1.7;overflow-wrap:anywhere;}
.meeting-details {display:grid;grid-template-columns:130px minmax(0,1fr);gap:10px 16px;line-height:1.75;margin:14px 0;}
.meeting-details dt {color:var(--text-secondary);font-size:13px;}
.meeting-details dd {margin:0;white-space:pre-wrap;overflow-wrap:anywhere;}
.meeting-record {padding-top:12px;border-top:1px solid var(--border);}
.record-footer {display:flex;flex-wrap:wrap;gap:8px 14px;margin-top:12px;}
.record-footer :deep(.el-button) {white-space:normal;height:auto;line-height:1.6;text-align:left;margin:0;}
.report-presets {display:flex;align-items:center;flex-wrap:wrap;gap:8px;margin:0 0 18px;font-size:13px;}
.report-presets > span {color:var(--text-secondary);margin-right:4px;}
.report-presets button {font:inherit;color:var(--text-secondary);background:transparent;border:1px solid var(--border);border-radius:6px;padding:5px 11px;cursor:pointer;}
.report-presets button:hover {color:var(--primary);border-color:var(--primary);}
.report-presets button.active {color:var(--primary);background:var(--primary-soft);border-color:var(--primary);}
.report-presets small {font-size:12px;color:var(--text-secondary);margin-left:auto;}
.report-filter-grid {display:grid;grid-template-columns:minmax(290px,1fr) minmax(150px,.55fr) auto;gap:14px;align-items:end;padding-bottom:20px;border-bottom:1px solid var(--border);}
.report-period-field {min-width:0;}
.report-period-field :deep(.el-date-editor) {width:100%;box-sizing:border-box;}
.report-period-field :deep(.el-range-input) {font-size:13px;}
.report-period-field :deep(.el-range-separator) {font-size:12px;color:var(--text-secondary);flex:0 0 20px;}
.report-pending {font-size:12px;color:var(--primary);line-height:1.75;margin:12px 0 0;}
.report-result-heading {display:flex;align-items:baseline;gap:8px 16px;flex-wrap:wrap;margin:22px 0 16px;}
.report-result-heading h3 {font-size:16px;font-weight:600;line-height:1.5;margin:0;}
.report-result-heading > span {font-size:12px;color:var(--text-secondary);}
.report-summary {display:grid;grid-template-columns:1fr 1fr 1.5fr;padding:16px 18px;background:var(--bg);border-radius:8px;margin:0 0 20px;gap:20px;}
.report-summary dt {font-size:12px;color:var(--text-secondary);margin-bottom:7px;}
.report-summary dd {font-size:25px;line-height:1.4;font-weight:600;margin:0;font-variant-numeric:tabular-nums;}
.report-summary dd small {font-size:12px;font-weight:400;color:var(--text-secondary);margin-left:6px;}
.report-summary-note {border-left:1px solid var(--border);padding-left:20px;}
.report-summary-note dd {font-size:13px;font-weight:400;}
.report-summary-note dd small {display:block;margin:6px 0 0;line-height:1.6;}
.report-layout {display:grid;grid-template-columns:minmax(0,.9fr) minmax(0,1.1fr);gap:18px;align-items:start;}
.report-chart,.report-breakdown {border:1px solid var(--border);border-radius:8px;padding:16px;min-width:0;}
.block-heading {display:flex;align-items:baseline;justify-content:space-between;gap:8px;flex-wrap:wrap;margin-bottom:16px;}
.block-heading h3 {font-size:15px;font-weight:600;margin:0;}
.block-heading span {font-size:12px;color:var(--text-secondary);}
.trend {padding:4px 0 0;margin:0;max-height:390px;overflow:auto;}
.trend-row {display:flex;gap:14px;align-items:center;margin:18px 0;}
.trend-row strong {width:80px;flex-shrink:0;font-size:12px;font-weight:500;color:var(--text-secondary);}
.trend-bars {flex:1;min-width:0;}
.trend-metric {display:flex;gap:8px;align-items:center;margin:7px 0;}
.trend-track {height:13px;flex:1;min-width:0;background:var(--bg);border-radius:3px;overflow:hidden;}
.bar {display:block;height:100%;border-radius:3px;}
.received {background:var(--primary);}
.filed {background:#55847b;}
.trend-metric button {width:32px;padding:0;background:none;border:0;color:var(--primary);font-family:inherit;font-size:13px;font-weight:500;line-height:1.5;font-variant-numeric:tabular-nums;cursor:pointer;}
.trend-metric button:disabled,.report-breakdown .text-action:disabled {color:var(--text-secondary);cursor:default;}
.report-legend {display:flex;gap:7px;align-items:center;flex-wrap:wrap;color:var(--text-secondary);font-size:12px;}
.received-dot,.filed-dot {display:inline-block;width:8px;height:8px;border-radius:2px;background:var(--primary);}
.filed-dot {background:#55847b;margin-left:9px;}
.report-quality {padding-top:20px;margin-top:22px;border-top:1px solid var(--border);}
.report-quality .block-heading {margin-bottom:12px;}
.report-quality .unknown-row {margin:0 0 10px;}
.report-quality strong {font-size:13px;font-weight:600;margin-left:7px;}
.report-topics {padding-top:18px;margin-top:20px;border-top:1px solid var(--border);}
.report-topics summary {font-size:14px;font-weight:500;color:var(--text);}
.report-topics summary span {font-size:12px;font-weight:400;color:var(--text-secondary);margin-left:12px;}
.report-topics > p {margin:12px 0;}
:global(.casework-month-picker .el-date-range-picker) {width:min(646px, calc(100vw - 24px));}
:global(.casework-month-picker .el-picker-panel__body) {min-width:0;}
@media (max-width:600px) {
  :global(.casework-month-picker .el-date-range-picker__content) {padding:12px 8px;}
  :global(.casework-month-picker .el-date-range-picker__header div) {font-size:14px;margin-right:20px;}
  :global(.casework-month-picker .el-month-table td) {width:25%;}
  :global(.casework-month-picker .el-date-table-cell__text) {width:42px;}
}
.person-row :deep(.el-select) {flex:1;min-width:140px;}
@container (max-width:900px) {.report-filter-grid {grid-template-columns:minmax(0,1fr) 180px;}.report-filter-grid .filter-submit {grid-column:1/-1;justify-content:flex-end;}}
@container (max-width:600px) {.report-filter-grid {grid-template-columns:minmax(0,1fr);}.report-filter-grid .filter-submit {justify-content:flex-start;}.report-presets small {width:100%;margin:4px 0 0;}.report-summary {grid-template-columns:1fr 1fr;}.report-summary-note {grid-column:1/-1;border-left:0;padding:12px 0 0;border-top:1px solid var(--border);}}
.unknown-row,.cw-actions {display:flex;flex-wrap:wrap;gap:8px;margin:16px 0 12px;}
.unknown-row :deep(.el-button),.cw-actions :deep(.el-button) {margin:0;}
.subsection-title {font-size:16px;font-weight:600;margin:26px 0 8px;padding-top:20px;border-top:1px solid var(--border);}
.recommend-form {border:1px solid var(--border);border-radius:8px;background:var(--bg);padding:16px;margin-bottom:20px;}
.recommend-form :deep(.el-textarea__inner) {font:inherit;line-height:1.7;padding:10px 12px;}
.candidate-stats {display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin:16px 0;padding:16px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border);}
.candidate-stats dt {color:var(--text-secondary);font-size:12px;margin-bottom:6px;}
.candidate-stats dd {font-size:22px;font-weight:600;margin:0;line-height:1.4;font-variant-numeric:tabular-nums;}
.candidate-stats small {font-size:12px;font-weight:400;color:var(--text-secondary);margin-left:5px;}
.cw-result > details:not(.meeting-record) {border-top:1px solid var(--border);padding-top:12px;margin-top:12px;}
.cw-panel summary,.cw-detail summary {cursor:pointer;line-height:1.7;color:var(--text-secondary);font-size:13px;}
.path {background:var(--bg);border-radius:6px;padding:10px 12px;font-size:13px;line-height:1.7;}
.path,.wrap {overflow-wrap:anywhere;white-space:pre-wrap;}
.deployment-note {color:var(--text-secondary);background:var(--bg);border-radius:6px;padding:12px;font-size:12px;line-height:1.75;}
.recommendation-method {padding:0 0 16px;}
.cw-detail-loading {min-height:180px;}
.cw-detail {font-size:14px;line-height:1.7;}
.cw-detail h2 {font-size:20px;overflow-wrap:anywhere;}
.field-row {display:flex;align-items:baseline;flex-wrap:wrap;gap:10px;justify-content:space-between;border-bottom:1px solid var(--border);padding:14px 0;}
.field-row p {margin:6px 0;overflow-wrap:anywhere;}
.field-row details {width:100%;}
.field-row strong {overflow-wrap:anywhere;}
.field-state {font-size:12px;color:var(--text-secondary);margin-left:12px;}
.field-state.conflict,.field-state.confirmation_stale {color:var(--warning);}
.field-state.confirmed {color:var(--primary);}
.person-row {display:flex;gap:10px;align-items:center;margin:14px 0;}
.person-row strong {width:130px;flex-shrink:0;}
.person-row .el-input {flex:1;}
.cw-panel :deep(.el-alert) {margin:12px 0;}
.cw-panel :deep(.el-table) {font-size:13px;border:1px solid var(--border);border-radius:7px;--el-table-header-bg-color:var(--bg);}
.cw-panel :deep(.el-table th.el-table__cell) {font-size:12px;font-weight:500;color:var(--text-secondary);padding:10px 0;}
.cw-panel :deep(.el-table td.el-table__cell) {padding:11px 0;}
.cw-panel :deep(.el-table .cell) {line-height:1.65;}
.cw-panel :deep(.el-table pre) {margin:8px 0;font-size:12px;max-height:300px;overflow:auto;background:var(--bg);padding:10px;border-radius:5px;}
@container (max-width:1050px) {
  .cases-filters {grid-template-columns:repeat(3,minmax(0,1fr));}
  .report-layout {grid-template-columns:minmax(0,1fr);}
  .assign-filters {grid-template-columns:minmax(0,1fr) minmax(0,1fr);}
  .assign-filters .filter-submit {grid-column:1/-1;}
}
@container (max-width:700px) {
  .cw-header {align-items:stretch;flex-direction:column;gap:12px;}
  .cw-context {margin-left:0;justify-content:space-between;}
  .workspace-metrics {gap:10px 16px;}
  .cw-content {padding:0;}
  .cw-filters {grid-template-columns:repeat(2,minmax(0,1fr));padding:12px;}
  .search-form .field-wide,.meeting-filters .field-wide {grid-column:1/-1;}
  .cw-filters .filter-submit {grid-column:1/-1;}
  .assign-filters {padding:0;}
  .search-guidance {padding:20px 0;}
  .meeting-participants {grid-template-columns:1fr;gap:10px;}
  .meeting-participants > div {display:grid;grid-template-columns:80px minmax(0,1fr);gap:10px;}
  .meeting-details {grid-template-columns:1fr;gap:4px;}
  .meeting-details dd {margin-bottom:10px;}
  .candidate-stats {grid-template-columns:repeat(3,minmax(0,1fr));row-gap:18px;}
  .person-row {flex-wrap:wrap;}
}
@container (max-width:420px) {
  .cw-filters {grid-template-columns:minmax(0,1fr);}
  .workspace-metrics {gap:10px;}
  .filter-submit {justify-content:flex-start;flex-wrap:wrap;}
  .search-examples {flex-direction:column;}
  .candidate-stats {grid-template-columns:repeat(2,minmax(0,1fr));}
}
@media (max-width:650px) {.cw-panel {padding:16px;}}

.meeting-colleagues {margin:0 0 20px;padding:0 0 18px;border-bottom:1px solid var(--border);}
.colleague-heading {display:flex;align-items:baseline;flex-wrap:wrap;gap:6px 12px;margin-bottom:12px;}
.colleague-heading strong {font-size:13px;font-weight:500;}
.colleague-heading > span {font-size:12px;color:var(--text-secondary);}
.colleague-list {display:flex;flex-wrap:wrap;gap:10px 24px;}
.colleague-item {display:flex;align-items:baseline;flex-wrap:wrap;gap:5px 9px;font-size:12px;}
.colleague-item strong {font-size:13px;font-weight:500;color:var(--text);}
.colleague-item span {color:var(--text-secondary);}
.meeting-case-title {overflow:hidden;white-space:nowrap;text-overflow:ellipsis;}
.meeting-scope-note {font-size:12px;color:var(--text-secondary);line-height:1.75;margin:14px 0 0;}
.meeting-detail {font-size:14px;line-height:1.75;color:var(--text);}
.meeting-detail-heading > span {font-size:13px;color:var(--text-secondary);}
.meeting-detail h2 {font-size:20px;line-height:1.5;margin:6px 0 8px;overflow-wrap:anywhere;}
.meeting-detail .meeting-participants {grid-template-columns:repeat(2,minmax(0,1fr));padding:16px 0;row-gap:14px;}
.meeting-detail-section {border-top:1px solid var(--border);padding:18px 0;}
.meeting-detail h3 {font-size:14px;font-weight:600;margin:0 0 8px;}
.meeting-detail-section p {margin:0;white-space:pre-wrap;overflow-wrap:anywhere;}
.meeting-technical {border-top:1px solid var(--border);padding:16px 0;}
.meeting-technical summary {font-size:13px;cursor:pointer;color:var(--text-secondary);}
.meeting-originals {border-top:1px solid var(--border);padding-top:18px;}
.meeting-source-link {display:grid;grid-template-columns:minmax(0,1fr) auto;gap:3px 12px;width:100%;text-align:left;border:1px solid var(--border);border-radius:7px;padding:12px;background:var(--bg);color:var(--text);font:inherit;font-size:13px;cursor:pointer;margin-top:10px;}
.meeting-source-link > span:first-child {overflow-wrap:anywhere;}
.meeting-source-link small {grid-column:1;font-size:12px;color:var(--text-secondary);overflow-wrap:anywhere;}
.meeting-source-link .source-link-action {grid-column:2;grid-row:1/3;align-self:center;color:var(--primary);white-space:nowrap;font-size:12px;}
.meeting-source-link:focus-visible {outline:2px solid var(--primary);outline-offset:2px;}
@media (max-width:560px) {.meeting-detail .meeting-participants {grid-template-columns:1fr;}.meeting-source-link {grid-template-columns:minmax(0,1fr);}.meeting-source-link .source-link-action {grid-column:1;grid-row:auto;}}

.sync-section-heading {display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:18px;}
.sync-directory-summary {display:flex;align-items:baseline;flex-wrap:wrap;gap:6px 16px;}
.sync-directory-count {font-size:13px;color:var(--text-secondary);}
.sync-directory-count strong {font-weight:500;color:var(--text);}
.sync-directory-hint {font-size:12px;color:var(--text-secondary);}
.sync-section-heading > .el-button {flex-shrink:0;}
.sync-section-heading :deep(.el-icon) {margin-right:6px;}
.sync-root {border:1px solid var(--border);border-radius:8px;padding:16px 18px;margin-bottom:12px;}
.sync-root-heading,.sync-root-name,.sync-root-action {display:flex;align-items:center;gap:10px;}
.sync-root-heading {justify-content:space-between;gap:12px;}
.sync-root-name {min-width:0;flex-wrap:wrap;gap:6px 9px;}
.sync-root-name > .el-icon {color:var(--chart-muted);font-size:18px;}
.sync-root-name h3 {font-size:15px;line-height:1.6;font-weight:600;margin:0;overflow-wrap:anywhere;}
.sync-classification {color:var(--text-secondary);font-size:12px;}
.sync-root-action {flex-shrink:0;gap:14px;}
.sync-root-meta {display:grid;grid-template-columns:1.3fr 1fr 1fr;gap:12px;margin:16px 0;}
.sync-root-meta dt {color:var(--text-secondary);font-size:12px;margin-bottom:4px;}
.sync-root-meta dd {font-size:13px;line-height:1.6;margin:0;font-variant-numeric:tabular-nums;}
.sync-version-note {color:var(--text-secondary);font-size:12px;}
.sync-maintenance {padding-top:10px;border-top:1px solid var(--border);}
.sync-maintenance summary {font-size:12px;}
.sync-directory-path {font-size:12px;margin-top:12px;}
.sync-directory-path > span {color:var(--text-secondary);}
.sync-directory-path p {margin:5px 0 12px;overflow-wrap:anywhere;line-height:1.7;background:var(--bg);padding:8px 10px;border-radius:5px;}
.sync-maintenance-actions {display:flex;flex-wrap:wrap;gap:8px;}
.sync-maintenance-actions :deep(.el-button + .el-button) {margin-left:0;}
.sync-root-error {font-size:13px;line-height:1.7;color:var(--danger);overflow-wrap:anywhere;}
.sync-pending {display:flex;align-items:center;flex-wrap:wrap;gap:8px 14px;color:var(--warning);font-size:13px;padding:10px 0;}
.sync-history-heading {display:flex;align-items:center;justify-content:space-between;gap:12px;margin:22px 0 12px;}
.sync-history-heading h3 {font-size:15px;font-weight:600;margin:0;}
.sync-job-detail {padding:12px 22px;font-size:13px;line-height:1.7;}
.sync-job-detail > p {margin:0 0 8px;}
.sync-job-detail h4 {font-size:13px;margin:16px 0 8px;}
.sync-job-counts {display:flex;flex-wrap:wrap;gap:16px 28px;margin:12px 0;}
.sync-job-counts dt {font-size:12px;color:var(--text-secondary);}
.sync-job-counts dd {font-size:15px;margin:4px 0 0;}
.sync-count-warning {color:var(--warning);}
.sync-deployment {margin-top:16px;}
.sync-deployment summary {font-size:12px;}
@container (max-width:700px) {
  .sync-section-heading {flex-wrap:wrap;}
  .sync-root {padding:14px;}
  .sync-root-heading {align-items:flex-start;flex-direction:column;}
  .sync-root-action {justify-content:space-between;width:100%;margin-top:3px;}
  .sync-root-meta {grid-template-columns:1fr 1fr;}
  .sync-root-meta > div:first-child {grid-column:1/-1;}
}

.audit-toolbar {display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;margin-bottom:16px;font-size:13px;}
.audit-toolbar strong {font-weight:600;}
.audit-sort {color:var(--text-secondary);font-size:12px;}
.audit-filter {display:flex;align-items:center;gap:10px;}
.audit-filter label {color:var(--text-secondary);font-size:13px;white-space:nowrap;}
.audit-filter .el-select {width:180px;}
.audit-action-name {font-weight:500;color:var(--text);}
.audit-actor-note,.audit-change-note {font-size:12px;line-height:1.75;color:var(--text-secondary);}
.audit-actor-note {margin:14px 0 0;}
.audit-detail {color:var(--text);font-size:14px;line-height:1.7;}
.audit-detail h2 {font-size:20px;line-height:1.5;margin:0 0 8px;}
.audit-detail-summary {font-size:13px;color:var(--text-secondary);margin:0 0 18px;overflow-wrap:anywhere;}
.audit-detail-meta {display:grid;grid-template-columns:1fr 1fr;gap:18px 24px;padding:18px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border);margin:0 0 20px;}
.audit-detail-meta dt {font-size:12px;color:var(--text-secondary);margin-bottom:5px;}
.audit-detail-meta dd {font-size:13px;margin:0;overflow-wrap:anywhere;white-space:pre-wrap;}
.audit-changes h3 {font-size:14px;font-weight:600;margin:0;}
.audit-change-note {margin:6px 0 14px;}
.audit-diff-heading,.audit-diff-row {display:grid;grid-template-columns:120px minmax(0,1fr) minmax(0,1fr);gap:14px;padding:12px 10px;border-bottom:1px solid var(--border);font-size:13px;}
.audit-diff-heading {background:var(--bg);color:var(--text-secondary);font-size:12px;}
.audit-diff-row strong {font-size:13px;font-weight:500;}
.audit-diff-row > span {white-space:pre-wrap;overflow-wrap:anywhere;}
.audit-diff-row > span:nth-child(2) {color:var(--text-secondary);}
.audit-raw {margin-top:20px;border-top:1px solid var(--border);padding-top:16px;}
.audit-raw summary {font-size:12px;color:var(--text-secondary);cursor:pointer;}
.audit-raw pre {white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.75 ui-monospace,monospace;padding:12px;background:var(--bg);border-radius:6px;max-height:360px;overflow:auto;}
@media (max-width:560px) {.audit-detail-meta {grid-template-columns:1fr;}.audit-diff-heading,.audit-diff-row {grid-template-columns:85px minmax(0,1fr) minmax(0,1fr);gap:8px;font-size:12px;padding:10px 4px;}}

/* 业务页面：统一留白与类别强调色。 */
.cw-panel {--business-accent:#245dcc;--business-success:#207351;--business-review:#966318;padding:20px;}
:global([data-theme='dark'] .cw-panel) {--business-accent:#8bb5ff;--business-success:#80d8ae;--business-review:#e7c17c;}
.cw-header {margin-bottom:16px;padding-bottom:16px;gap:12px 20px;}
.cw-filters {padding:14px;margin-bottom:16px;gap:12px;}
.cw-content :deep(.el-table__cell) {padding:12px 0;}
.cw-content :deep(.el-table .cell) {padding:0 12px;line-height:1.65;word-break:normal;}
.module-cases :deep(.el-table .cell) {white-space:nowrap;}
.module-cases :deep(.el-table th .cell) {white-space:normal;}
.colleague-heading {display:list-item;cursor:pointer;color:var(--business-accent);font-size:13px;font-weight:500;margin:0;}
.colleague-heading span {margin-left:10px;color:var(--text-secondary);font-weight:400;}
.meeting-colleagues {padding:10px 12px;margin-bottom:16px;border:1px solid var(--border);border-radius:6px;background:var(--card-bg);}
.colleague-list {display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:0 20px;margin-top:12px;}
.colleague-item {padding:10px 0;border-top:1px solid var(--border);display:grid;grid-template-columns:1fr;gap:2px;}
.recommend-form {padding:16px;background:var(--card-bg);border-color:color-mix(in srgb,var(--business-accent) 24%,var(--border));}
.recommend-form > .filter-field > label {color:var(--business-accent);font-weight:600;border-left:3px solid currentColor;padding-left:8px;}
.recommend-form :deep(.el-textarea__inner) {background:color-mix(in srgb,var(--business-accent) 4%,var(--card-bg));box-shadow:0 0 0 1px color-mix(in srgb,var(--business-accent) 24%,var(--border)) inset;}
.recommend-form :deep(.el-textarea__inner:focus) {box-shadow:0 0 0 1px var(--primary) inset;}
.assign-filters {padding:14px 0 0;margin-top:14px;border-top:1px solid var(--border);border-radius:0;}
.report-controls {padding:14px;border:1px solid var(--border);border-radius:8px;background:var(--bg);}
.report-presets {margin-bottom:12px;gap:6px;}
.report-presets small {display:none;}
.report-filter-grid {padding:0;border:0;gap:12px;grid-template-columns:minmax(240px,1fr) minmax(140px,.5fr) auto;}
.report-result-heading {margin:18px 0 12px;}
.report-summary {padding:0;background:none;gap:12px;grid-template-columns:1fr 1fr;}
.report-summary > div:not(.report-summary-note) {padding:12px 16px;border:1px solid color-mix(in srgb,var(--business-accent) 18%,var(--border));border-radius:8px;background:color-mix(in srgb,var(--business-accent) 4%,var(--card-bg));color:var(--business-accent);}
.report-summary > div:nth-child(2) {color:var(--business-success);border-color:color-mix(in srgb,var(--business-success) 18%,var(--border));background:color-mix(in srgb,var(--business-success) 4%,var(--card-bg));}
.report-summary-note {grid-column:1/-1;border:0;padding:0;display:flex;gap:12px;align-items:baseline;}
.report-summary-note dt {margin:0;white-space:nowrap;}
.report-summary-note dd small {display:inline;margin:0 0 0 12px;}
.report-layout {grid-template-columns:minmax(0,1fr);gap:16px;}
.report-chart,.report-breakdown {padding:14px 16px;}
.report-chart .block-heading h3 {color:var(--business-accent);}
.report-chart .block-heading {margin-bottom:10px;}
.trend {max-height:280px;padding-top:8px;}
.trend-row {margin:10px 0;}
.trend-row:first-child {margin-top:0;}
.trend-row:last-child {margin-bottom:0;}
.trend-metric {margin:3px 0;}
.trend-track {height:9px;}
.filed,.filed-dot {background:var(--business-success);}
.metric-filed button:enabled,.report-breakdown .filed-count:enabled {color:var(--business-success);}
.trend-metric button:focus-visible,.report-breakdown .text-action:focus-visible,.colleague-heading:focus-visible {outline:2px solid var(--business-accent);outline-offset:2px;border-radius:3px;}
.report-breakdown .block-heading h3 {color:var(--business-success);}
.report-quality .block-heading h3 {color:var(--business-review);}
.sync-root {border-left:3px solid var(--business-accent);background:color-mix(in srgb,var(--business-accent) 2%,var(--card-bg));padding:16px;margin-bottom:16px;}
.sync-root-name > .el-icon,.sync-root-name h3 {color:var(--business-accent);}
.sync-classification {background:var(--bg);border-radius:4px;padding:2px 7px;}
.sync-root-meta {padding:12px;background:var(--card-bg);border:1px solid var(--border);border-radius:6px;margin:14px 0;}
.sync-root-meta .sync-state-ready {color:var(--business-success);font-weight:500;}
.sync-root-meta .sync-state-error {color:var(--danger,#c34343);font-weight:500;}
.sync-root-disabled {border-left-color:var(--text-secondary);background:var(--bg);}
.sync-root-disabled .sync-root-name h3,.sync-root-disabled .sync-root-name > .el-icon {color:var(--text-secondary);}
.sync-maintenance summary {color:var(--business-accent);}
@container (max-width:700px) {.report-filter-grid {grid-template-columns:1fr;}.report-summary-note {display:block;}.report-summary-note dd small {display:block;margin:4px 0 0;}.assign-filters {grid-template-columns:1fr;}}

</style>
