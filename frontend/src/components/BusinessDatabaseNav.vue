<template>
  <nav class="business-nav" aria-label="业务数据库功能">
    <h2>业务数据库</h2>
    <div class="nav-group">
      <div class="nav-items">
        <button v-for="item in records" :key="item.table" type="button"
          :class="{active: !settingsVisible && activeTab === 'browse' && recordTable === item.table}"
          :aria-current="!settingsVisible && activeTab === 'browse' && recordTable === item.table ? 'page' : undefined"
          @click="selectRecord(item.table)">
          <el-icon aria-hidden="true"><component :is="item.icon" /></el-icon>{{ item.label }}
        </button>
      </div>
    </div>
    <div class="nav-group management">
      <p>数据管理</p>
      <div class="nav-items">
        <button v-for="item in management" :key="item.tab" type="button"
          :class="{active: !settingsVisible && activeTab === item.tab}"
          :aria-current="!settingsVisible && activeTab === item.tab ? 'page' : undefined"
          @click="selectTab(item.tab)">
          <el-icon aria-hidden="true"><component :is="item.icon" /></el-icon>{{ item.label }}
        </button>
        <button type="button" :class="{active: settingsVisible}" aria-haspopup="dialog"
          :aria-expanded="settingsVisible" @click="settingsVisible = true">
          <el-icon aria-hidden="true"><Setting /></el-icon>数据库设置
        </button>
      </div>
    </div>
  </nav>
</template>

<script setup>
import { Briefcase, ChatLineSquare, FolderOpened, PriceTag, Grid, Upload, Clock, Setting } from '@element-plus/icons-vue'
import { BUSINESS_RECORDS } from '../businessRecords'

const recordTable = defineModel('recordTable', { type: String, required: true })
const activeTab = defineModel('activeTab', { type: String, required: true })
const settingsVisible = defineModel('settingsVisible', { type: Boolean, required: true })
const icons = [Briefcase, ChatLineSquare, FolderOpened, PriceTag]
const records = BUSINESS_RECORDS.map((item, index) => ({ ...item, icon: icons[index] }))
  .concat({ table: 'other', label: '其他数据', icon: Grid })
const management = [{ tab: 'import', label: '数据导入', icon: Upload }, { tab: 'audit', label: '修改记录', icon: Clock }]

function selectRecord(table) {
  recordTable.value = table
  selectTab('browse')
}
function selectTab(tab) {
  activeTab.value = tab
  settingsVisible.value = false
}
</script>

<style scoped>
.business-nav { padding: 6px 2px; }
h2 { margin: 0 10px 22px; color: var(--text); font-size: 16px; font-weight: 600; }
.nav-group p { margin: 0 10px 8px; font-size: 12px; letter-spacing: .02em; color: var(--text-secondary); }
.nav-items { display: flex; flex-direction: column; gap: 4px; }
button { display: flex; align-items: center; gap: 10px; width: 100%; min-height: 42px; padding: 10px 12px; border: 0; border-radius: 7px; background: transparent; color: var(--text-secondary); font: inherit; font-size: 14px; letter-spacing: .015em; text-align: left; white-space: nowrap; cursor: pointer; }
button .el-icon { flex-shrink: 0; font-size: 17px; }
button:hover { color: var(--text); background: var(--primary-soft); }
button.active { color: var(--primary); background: var(--primary-soft); font-weight: 600; box-shadow: inset 3px 0 var(--primary); }
button:focus-visible { outline: 2px solid var(--primary); outline-offset: -2px; }
.management { margin-top: 24px; padding-top: 18px; border-top: 1px solid var(--border); }
@media (max-width: 1100px) {
  .business-nav { padding: 0; }
  h2 { display: none; }
  .nav-group { display: flex; align-items: center; gap: 8px; min-width: 0; }
  .nav-group p { flex: 0 0 52px; margin: 0; white-space: nowrap; }
  .nav-items { flex: 1; min-width: 0; flex-direction: row; overflow-x: auto; gap: 4px; padding: 2px; }
  button { width: auto; min-height: 36px; padding: 8px 10px; gap: 6px; flex-shrink: 0; }
  button.active { box-shadow: inset 0 -2px var(--primary); }
  .management { margin-top: 4px; padding-top: 4px; }
}
</style>
