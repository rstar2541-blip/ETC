#!/usr/bin/env node
// 트렌드 라이터 PC용 로컬 서버.
// 페이지를 띄우고, 브라우저가 직접 부를 수 없는 네이버 API 요청만 대신 전달한다.
// 실행: node server.mjs  →  http://localhost:8000
import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const ROOT = path.dirname(fileURLToPath(import.meta.url));
const PORT = Number(process.env.PORT) || 8000;

// 전달 대상은 네이버 API 두 곳뿐이다. (환경 변수는 테스트에서 가짜 서버를 가리킬 때만 쓴다.)
const TARGETS = {
  legacy: {
    base: process.env.TW_NAVER_LEGACY_BASE || 'https://openapi.naver.com',
    paths: ['/v1/datalab/', '/v1/search/'],
    headers: (id, secret) => ({ 'X-Naver-Client-Id': id, 'X-Naver-Client-Secret': secret }),
  },
  hub: {
    base: process.env.TW_NAVER_HUB_BASE || 'https://naverapihub.apigw.ntruss.com',
    paths: ['/search-trend/', '/search/'],
    headers: (id, secret) => ({ 'X-NCP-APIGW-API-KEY-ID': id, 'X-NCP-APIGW-API-KEY': secret }),
  },
};
const STATIC = { '/': 'index.html', '/index.html': 'index.html', '/vendor/anthropic-sdk.js': 'vendor/anthropic-sdk.js' };
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8' };
const MAX_IMAGE = 25 * 1024 * 1024;

// 이미지 내려받기는 방금 이미지 검색 결과에 나온 주소만 허용한다.
const seenImages = new Set();
function rememberImages(text) {
  try {
    for (const it of JSON.parse(text).items || []) if (it.link) seenImages.add(it.link);
    while (seenImages.size > 2000) seenImages.delete(seenImages.values().next().value);
  } catch {}
}

function send(res, status, body, headers = {}) {
  res.writeHead(status, { 'cache-control': 'no-store', ...headers });
  res.end(body);
}
const json = (res, status, obj) => send(res, status, JSON.stringify(obj), { 'content-type': 'application/json; charset=utf-8' });

async function readBody(req) {
  const chunks = []; let size = 0;
  for await (const c of req) { size += c.length; if (size > 1024 * 1024) throw new Error('too large'); chunks.push(c); }
  return Buffer.concat(chunks);
}

// 다른 사이트가 이 서버를 몰래 부르지 못하게, 같은 출처에서 온 요청만 받는다.
function sameOrigin(req) {
  const site = req.headers['sec-fetch-site'];
  if (site && site !== 'same-origin' && site !== 'none') return false;
  const origin = req.headers.origin;
  return !origin || origin === 'http://' + req.headers.host;
}

async function handleApi(req, res, url) {
  const m = url.pathname.match(/^\/naver\/api\/(legacy|hub)(\/.*)$/);
  const target = m && TARGETS[m[1]];
  if (!target || !target.paths.some(p => m[2].startsWith(p)) || m[2].includes('..')) return json(res, 404, { errorMessage: 'unknown api path' });
  if (req.method !== 'GET' && req.method !== 'POST') return json(res, 405, { errorMessage: 'method not allowed' });
  const id = req.headers['x-tw-id'], secret = req.headers['x-tw-secret'];
  if (!id || !secret) return json(res, 401, { errorMessage: 'missing naver key' });
  const body = req.method === 'POST' ? await readBody(req) : undefined;
  let r;
  try {
    r = await fetch(target.base + m[2] + url.search, {
      method: req.method,
      headers: { ...target.headers(id, secret), ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body,
    });
  } catch (e) { return json(res, 502, { errorMessage: '네이버 서버에 연결하지 못했어요: ' + e.message }); }
  const text = await r.text();
  if (r.ok && m[2].endsWith('/image')) rememberImages(text);
  send(res, r.status, text, { 'content-type': r.headers.get('content-type') || 'application/json; charset=utf-8' });
}

async function handleImage(res, url) {
  const src = url.searchParams.get('url') || '';
  if (!seenImages.has(src) || !/^https?:\/\//.test(src)) return json(res, 403, { errorMessage: 'image not from a recent search' });
  let r;
  try { r = await fetch(src, { headers: { 'User-Agent': 'Mozilla/5.0' } }); } catch { return json(res, 502, { errorMessage: 'fetch failed' }); }
  const type = r.headers.get('content-type') || '';
  if (!r.ok || !type.startsWith('image/')) return json(res, 502, { errorMessage: 'not an image' });
  const buf = Buffer.from(await r.arrayBuffer());
  if (buf.length > MAX_IMAGE) return json(res, 413, { errorMessage: 'image too large' });
  send(res, 200, buf, { 'content-type': type });
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname.startsWith('/naver/')) {
      if (!sameOrigin(req)) return json(res, 403, { errorMessage: 'cross-site request refused' });
      if (url.pathname === '/naver/ping') return json(res, 200, { ok: true });
      if (url.pathname === '/naver/image' && req.method === 'GET') return await handleImage(res, url);
      return await handleApi(req, res, url);
    }
    const file = STATIC[url.pathname];
    if (!file || req.method !== 'GET') return send(res, 404, 'Not found');
    send(res, 200, await readFile(path.join(ROOT, file)), { 'content-type': TYPES[path.extname(file)] });
  } catch (e) {
    json(res, 500, { errorMessage: e.message });
  }
});
server.listen(PORT, '127.0.0.1', () => console.log(`트렌드 라이터: http://localhost:${PORT}`));
