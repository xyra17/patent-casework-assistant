import { defineAsyncComponent, h } from 'vue'

export function loadPanel(loader, label) {
  return defineAsyncComponent({
    loader,
    delay: 150,
    timeout: 20000,
    loadingComponent: {
      render: () => h('div', { class: 'panel-load-state', role: 'status' }, `正在加载${label}…`),
    },
    errorComponent: {
      // 不自动刷新，避免丢失用户尚未提交的输入。
      render: () => h('div', { class: 'panel-load-state', role: 'alert' }, [
        h('strong', `${label}加载失败`),
        h('p', '页面可能已更新或连接中断。请保留未提交内容后刷新页面。'),
        h('button', { type: 'button', onClick: () => window.location.reload() }, '刷新页面'),
      ]),
    },
  })
}
