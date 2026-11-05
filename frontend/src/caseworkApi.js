import {checked} from './api.js'

export async function casework(path, {method='GET', body, params, signal} = {}) {
  const query = new URLSearchParams(Object.entries(params || {}).filter(([,v])=>v !== '' && v != null))
  const response = await fetch(`/api/casework${path}${query.size ? '?' + query : ''}`, {
    method, signal, headers:body ? {'Content-Type':'application/json'} : {}, body:body ? JSON.stringify(body) : undefined,
  })
  return (await checked(response)).json()
}
export const casePath = key => `/cases/${encodeURIComponent(key)}`
