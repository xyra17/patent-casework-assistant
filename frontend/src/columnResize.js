const states=new WeakMap()

function install(table) {
  const headers=[...(table.tHead?.rows[table.tHead.rows.length-1]?.cells||[])]
  const labels=headers.map(cell=>cell.textContent.trim())
  const previous=states.get(table)
  if(previous?.headers.length===headers.length&&headers.every((cell,i)=>cell===previous.headers[i]&&labels[i]===previous.labels[i]))return
  previous?.dispose()
  if(!headers.length||headers.some(cell=>cell.colSpan!==1))return
  const controller=new AbortController(),handles=[]
  const original={width:table.style.width,layout:table.style.tableLayout}
  let columns,group,drag=null
  const listen=(element,event,handler)=>element.addEventListener(event,handler,{signal:controller.signal})
  function prepare() {
    if(columns)return
    const widths=headers.map(cell=>cell.getBoundingClientRect().width)
    if(widths.some(width=>width<=0))return
    group=document.createElement('colgroup')
    columns=widths.map(width=>{const col=document.createElement('col');col.style.width=width+'px';group.append(col);return col})
    table.prepend(group);table.style.tableLayout='fixed';table.style.width=widths.reduce((sum,width)=>sum+width,0)+'px'
  }
  function resize(index,width) {
    prepare();if(!columns)return
    const next=Math.max(72,Math.round(width))
    columns[index].style.width=next+'px'
    table.style.width=columns.reduce((sum,col)=>sum+parseFloat(col.style.width),0)+'px'
    handles[index].setAttribute('aria-valuenow',String(next))
  }
  function finish() {drag=null;table.classList.remove('is-column-resizing')}
  for(const [index,header] of headers.entries()) {
    const handle=document.createElement('button')
    handle.type='button';handle.className='table-column-resize-handle'
    handle.setAttribute('role','separator');handle.setAttribute('aria-orientation','vertical');handle.setAttribute('aria-valuemin','72')
    handle.setAttribute('aria-label','调整'+labels[index]+'列宽');handle.title='左右拖动调整列宽；方向键微调'
    handles.push(handle);header.append(handle)
    listen(handle,'pointerdown',event=>{
      if(event.button!==0)return
      event.preventDefault();event.stopPropagation();prepare();if(!columns)return
      drag={index,x:event.clientX,width:parseFloat(columns[index].style.width),pointerId:event.pointerId}
      handle.setPointerCapture(event.pointerId);table.classList.add('is-column-resizing')
    })
    listen(handle,'pointermove',event=>{if(drag?.index===index&&drag.pointerId===event.pointerId)resize(index,drag.width+event.clientX-drag.x)})
    listen(handle,'pointerup',finish);listen(handle,'pointercancel',finish);listen(handle,'lostpointercapture',finish)
    listen(handle,'keydown',event=>{
      if(!['ArrowLeft','ArrowRight'].includes(event.key))return
      event.preventDefault();event.stopPropagation();prepare()
      if(columns)resize(index,parseFloat(columns[index].style.width)+(event.key==='ArrowRight'?1:-1)*(event.shiftKey?32:8))
    })
  }
  table.classList.add('native-resizable-table')
  states.set(table,{headers,labels,dispose(){controller.abort();handles.forEach(handle=>handle.remove());group?.remove();table.style.width=original.width;table.style.tableLayout=original.layout;finish()}})
}

export const resizableColumns={mounted:install,updated:install,beforeUnmount(table){states.get(table)?.dispose();states.delete(table)}}
