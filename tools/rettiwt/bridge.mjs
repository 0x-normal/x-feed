// One read-only operation per process. Secrets arrive through an anonymous pipe.
import { Rettiwt } from 'rettiwt-api';
import axios from 'axios';

let lastRequest = 0;
axios.defaults.timeout = 30000;
axios.interceptors.request.use(async config => {
  await new Promise(resolve => setTimeout(resolve, Math.max(0, lastRequest + 3000 - Date.now())));
  lastRequest = Date.now();
  return config;
});

export function cursorValue(next) {
  if (typeof next === 'string') return next;
  return typeof next?.value === 'string' ? next.value : '';
}

export function userData(user) {
  if (!user?.id || !user?.userName) throw Object.assign(new Error(), {safeKind: 'UserUnavailable'});
  return {id: user.id, screen_name: user.userName, name: user.fullName,
    following_count: user.followingsCount, avatar: user.profileImage,
    description: user.description ?? '',
    followers_count: Number.isSafeInteger(user.followersCount) && user.followersCount >= 0 ? user.followersCount : null};
}

export function tweetData(tweet) {
  const original = tweet.retweetedTweet || tweet.quoted;
  const kind = tweet.retweetedTweet ? 'repost' : tweet.quoted ? 'quote' : tweet.replyTo ? 'reply' : 'post';
  const context = {reply_to:tweet.replyTo || null};
  if (original) context.original = {id:original.id,text:original.fullText,author:original.tweetBy?.userName,
    name:original.tweetBy?.fullName,avatar:original.tweetBy?.profileImage,url:original.url};
  return {id: tweet.id, text: tweet.fullText, created_at: tweet.createdAt,
    favorite_count: tweet.likeCount, retweet_count: tweet.retweetCount,
    reply_count: tweet.replyCount, view_count: tweet.viewCount,kind,
    author:tweet.tweetBy?.userName,author_name:tweet.tweetBy?.fullName,avatar:tweet.tweetBy?.profileImage,
    context, published_at:Date.parse(tweet.createdAt)/1000};
}

export function safeError(error) {
  const status = error?.response?.status ?? error?.status;
  const codes = error?.response?.data?.errors ?? error?.details;
  const code = Array.isArray(codes) ? codes[0]?.code : undefined;
  const kinds = {401:'Unauthorized', 403:'Forbidden', 404:'NotFound', 429:'TooManyRequests'};
  const codeKinds = {32:'Unauthorized', 64:'AccountSuspended', 89:'InvalidSession', 326:'AccountLocked'};
  return {kind: codeKinds[code] ?? kinds[status] ?? error.safeKind ?? 'RettiwtError',
    reset: Number(error?.response?.headers?.['x-rate-limit-reset']) || null};
}

async function main() {
  try {
    let raw = '';
    for await (const chunk of process.stdin) raw += chunk;
    const {cookies, op, id, count, cursor} = JSON.parse(raw);
    const names = ['auth_token', 'ct0', 'twid'];
    if (names.some(name => typeof cookies[name] !== 'string' || !cookies[name])) {
      throw Object.assign(new Error(), {safeKind:'MissingSessionCookies'});
    }
    const apiKey = Buffer.from(names.map(name => `${name}=${cookies[name]};`).join('')).toString('base64');
    const client = new Rettiwt({apiKey, logging:false, timeout:30000, maxRetries:0,
      errorHandler:{handle(error) {throw error;}}});
    let data;
    if (op === 'self') data = userData(await client.user.details());
    else if (op === 'user') data = userData(await client.user.details(id));
    else if (op === 'posts' || op === 'replies' || op === 'following') {
      const page = op === 'posts' ? await client.user.timeline(id, Math.min(count, 20), cursor || undefined)
        : op === 'replies' ? await client.user.replies(id,Math.min(count,20),cursor || undefined)
        : await client.user.following(id, Math.min(count, 20), cursor || undefined);
      // Reply timelines contain conversation context by other authors; do not
      // attribute those context posts to the tracked account.
      const items = op === 'following' ? page.list : page.list.filter(t => t.tweetBy?.id === id);
      data = {items:items.map(op === 'following' ? userData : tweetData), next:cursorValue(page.next)};
    } else throw Object.assign(new Error(), {safeKind:'InvalidOperation'});
    console.log(JSON.stringify({ok:true, data}));
  } catch(error) {
    console.log(JSON.stringify({ok:false, error:safeError(error)}));
    process.exitCode = 2;
  }
}

// Keep cursor parsing importable by the local contract check without making requests.
if (process.argv[1] && new URL(import.meta.url).pathname.endsWith('/' + process.argv[1].replaceAll('\\', '/').split('/').at(-1))) {
  await main();
}
