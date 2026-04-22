import axios from 'axios'

const client = axios.create({ baseURL: '/api' })

export async function getPeriodos() {
  const { data } = await client.get('/periodos')
  return data
}

export async function getPreview(payload) {
  const { data } = await client.post('/preview', payload)
  return data
}

export async function ejecutarAutomatizacion(payload) {
  const { data } = await client.post('/ejecutar', payload)
  return data
}

export function getDownloadUrl(filename) {
  return `/api/descargar/${filename}`
}
