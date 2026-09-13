// Read-only, one-account probe. Receive secrets over stdin, never CLI arguments.
import { Rettiwt } from 'rettiwt-api';
import axios from 'axios';

let stage = 'input';
let lastResponse;
const observations = [];
const allowedHosts = new Set(['x.com', 'api.x.com', 'twitter.com', 'api.twitter.com', 'abs.twimg.com']);
axios.defaults.timeout = 20000;
axios.interceptors.request.use(config => {
  const host = new URL(config.url).hostname;
  if (!allowedHosts.has(host)) throw new Error('UnexpectedHost');
  return config;
});
function metadata(response) {
  if (!response) return undefined;
  const codes = response.data?.errors;
  return {
    host: new URL(response.config.url).hostname,
    httpStatus: response.status,
    contentType: response.headers?.['content-type']?.split(';')[0],
    xErrorCodes: Array.isArray(codes) ? codes.map(x => x.code).filter(Number.isInteger) : [],
    rateLimitReset: /^\d+$/.test(String(response.headers?.['x-rate-limit-reset'] ?? ''))
      ? Number(response.headers['x-rate-limit-reset']) : undefined,
  };
}
axios.interceptors.response.use(response => {
  lastResponse = metadata(response);
  return response;
}, error => {
  lastResponse = metadata(error.response);
  throw error;
});

function failure(error) {
  const known = {
    'Failed to get ondemand file': 'transaction_initialization_failed',
    "Couldn't get KEY_BYTE indices": 'transaction_initialization_failed',
    'SessionMismatch': 'session_identity_mismatch',
    'MissingCookies': 'missing_required_cookies',
  };
  return {ok: false, stage, reason: known[error?.message] ?? 'request_failed',
          response: lastResponse, observations};
}

try {
  let input = '';
  for await (const chunk of process.stdin) input += chunk;
  const {account, cookies, target} = JSON.parse(input);
  if (!/^[a-z0-9_]{1,15}$/i.test(account) || !/^[a-z0-9_]{1,15}$/i.test(target)) throw new Error('InvalidHandle');
  const names = ['auth_token', 'ct0', 'twid'];
  if (names.some(name => typeof cookies[name] !== 'string' || !cookies[name])) throw new Error('MissingCookies');
  const apiKey = Buffer.from(names.map(name => `${name}=${cookies[name]};`).join('')).toString('base64');
  const client = new Rettiwt({apiKey, logging: false, maxRetries: 0, delay: 3000, timeout: 20000,
    errorHandler: {handle(error) { throw error; }}});
  stage = 'account_details';
  const self = await client.user.details();
  if (self?.userName?.toLowerCase() !== account.toLowerCase()) throw new Error('SessionMismatch');
  observations.push({stage, ok: true});
  stage = 'target_details';
  const user = await client.user.details(target);
  if (!user?.id) throw new Error('TargetUnavailable');
  observations.push({stage, ok: true});
  stage = 'following';
  const following = await client.user.following(user.id, 20);
  observations.push({stage, ok: true, count: following.list.length, hasNext: Boolean(typeof following.next === 'string' ? following.next : following.next?.value)});
  stage = 'posts';
  const posts = await client.user.timeline(user.id, 20);
  observations.push({stage, ok: true, count: posts.list.length, hasNext: Boolean(typeof posts.next === 'string' ? posts.next : posts.next?.value)});
  console.log(JSON.stringify({ok: true, observations}));
} catch (error) {
  console.log(JSON.stringify(failure(error)));
  process.exitCode = 2;
}
