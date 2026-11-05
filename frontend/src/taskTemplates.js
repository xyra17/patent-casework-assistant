import catalog from '../../config/session-examples.json' with { type: 'json' }

export const TASK_GROUPS = catalog.groups
export const QUICK_TEMPLATES = catalog.templates

export function homeTemplates(templates) {
  const ids = ['similar-case', 'case-materials', 'meeting-history',
    'assignment-reference', 'business-charts', 'disclosure-report']
  const selected = ids.map(id => templates.find(item => item.id === id)).filter(Boolean)
  return selected.length ? selected : templates.slice(0, 6)
}

export function taskTemplates(experience = {}) {
  // Department templates have one shared catalog. Prefer the current bundle
  // when an already-running backend still holds an older catalog snapshot.
  if (experience.task_examples_catalog === 'department') return QUICK_TEMPLATES
  const configured = experience.quick_templates
  return configured?.length ? configured : QUICK_TEMPLATES
}

export function featuredTemplates(templates, experience = {}) {
  const ids = experience.featured_template_ids || catalog.featured_template_ids
  const selected = ids.map(id => templates.find(item => item.id === id)).filter(Boolean)
  return [...selected, ...templates.filter(item => !selected.includes(item))].slice(0, 3)
}

export function groupedTemplates(templates, group) {
  return templates.filter(item => (item.group || 'daily') === group)
}

export function attachmentRequirement(template, files) {
  const requirement = template?.attachments
  if (!requirement) return ''
  if (files.length < requirement.min || files.length > requirement.max) return requirement.hint
  const patterns = {
    tables: /\.(csv|xlsx|xls)$/i,
    documents: /\.(docx|pdf|txt|md)$/i,
    pdf: /\.pdf$/i,
    visual: /\.(pdf|png|jpe?g|webp|gif|bmp|tiff?)$/i,
  }
  const pattern = patterns[requirement.kind] || patterns.documents
  return files.every(file => pattern.test(file.name)) ? '' : requirement.hint
}

export function attachmentTaskContext(template, files) {
  if (template?.attachments?.min !== 2 || files.length !== 2) return ''
  return `\n【本次附件对比顺序】基准文件：${JSON.stringify(files[0].name)}；对比文件：${JSON.stringify(files[1].name)}。`
}
