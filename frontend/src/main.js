import { createApp, h } from 'vue'
import { ElConfigProvider } from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import { ElAlert, ElButton, ElDatePicker, ElSwitch, ElCheckbox, ElCheckboxGroup, ElDialog, ElDrawer, ElDropdown, ElDropdownItem, ElDropdownMenu, ElEmpty, ElForm, ElFormItem, ElIcon, ElInput, ElInputNumber, ElOption, ElPopover, ElProgress, ElRadio, ElRadioButton, ElRadioGroup, ElScrollbar, ElSelect, ElSkeleton, ElTabPane, ElTable, ElTableColumn, ElTabs, ElTag, ElTooltip, ElUpload, ElLoading } from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import { icons } from './icons'
import App from './App.vue'
import './style.css'
import { applyTheme } from './store'
import { resizableColumns } from './columnResize'

const app = createApp({render:()=>h(ElConfigProvider,{locale:zhCn},{default:()=>h(App)})})
for (const component of [ElAlert, ElButton, ElDatePicker, ElSwitch, ElCheckbox, ElCheckboxGroup, ElDialog, ElDrawer, ElDropdown, ElDropdownItem, ElDropdownMenu, ElEmpty, ElForm, ElFormItem, ElIcon, ElInput, ElInputNumber, ElOption, ElPopover, ElProgress, ElRadio, ElRadioButton, ElRadioGroup, ElScrollbar, ElSelect, ElSkeleton, ElTabPane, ElTable, ElTableColumn, ElTabs, ElTag, ElTooltip, ElUpload]) app.component(component.name, component)
app.use(ElLoading)
app.directive('resizable-columns',resizableColumns)
for (const [key, component] of Object.entries(icons)) {
  app.component(key, component)
}
applyTheme()
app.mount('#app')
