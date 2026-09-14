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

export function fullTweetText(tweet) {
  const note = tweet?.raw?.note_tweet?.note_tweet_results?.result?.text;
  if (typeof note === 'string' && note) return note;
  return typeof tweet?.fullText === 'string' ? tweet.fullText : '';
}

function mediaURL(value, host) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && url.hostname === host && !url.username && !url.password && !url.port ? url.href : '';
  } catch { return ''; }
}

export function mediaData(tweet) {
  const raw = tweet?.raw?.legacy;
  const media = raw?.extended_entities?.media ?? raw?.entities?.media ?? tweet?.media;
  if (!Array.isArray(media)) return [];
  return media.slice(0, 4).flatMap(item => {
    if (!item || typeof item !== 'object') return [];
    const type = {photo:'photo', video:'video', animated_gif:'gif', PHOTO:'photo', VIDEO:'video', GIF:'gif'}[item.type];
    if (!type) return [];
    const poster = mediaURL(item.media_url_https || item.thumbnailUrl, 'pbs.twimg.com');
    let url;
    if (type === 'photo') url = mediaURL(item.media_url_https || item.url, 'pbs.twimg.com');
    else {
      const variants = Array.isArray(item.video_info?.variants) ? item.video_info.variants : [];
      const playable = variants.filter(v => v?.content_type === 'video/mp4' && mediaURL(v.url, 'video.twimg.com'))
        .sort((a, b) => (Number(b.bitrate) || 0) - (Number(a.bitrate) || 0));
      url = mediaURL(playable[0]?.url || item.url, 'video.twimg.com');
      if (url && !new URL(url).pathname.toLowerCase().endsWith('.mp4')) url = '';
    }
    if (!url && !poster) return [];
    return [{type, url, poster: type === 'photo' ? '' : poster,
      alt: typeof item.ext_alt_text === 'string' ? item.ext_alt_text : ''}];
  });
}

function originalData(tweet, depth = 0) {
  const data = {id:tweet.id,text:fullTweetText(tweet),author:tweet.tweetBy?.userName,
    name:tweet.tweetBy?.fullName,avatar:tweet.tweetBy?.profileImage,url:tweet.url,media:mediaData(tweet)};
  if (tweet.quoted && depth < 2) data.quoted = originalData(tweet.quoted, depth + 1);
  return data;
}

export function tweetData(tweet) {
  const original = tweet.retweetedTweet || tweet.quoted;
  const kind = tweet.retweetedTweet ? 'repost' : tweet.quoted ? 'quote' : tweet.replyTo ? 'reply' : 'post';
  const context = {reply_to:tweet.replyTo || null, media:tweet.retweetedTweet ? [] : mediaData(tweet)};
  if (original) context.original = originalData(original);
  // X's outer RT text is only a shortened preview of the original caption.
  const text = tweet.retweetedTweet ? fullTweetText(original) || fullTweetText(tweet) : fullTweetText(tweet);
  return {id: tweet.id, text, created_at: tweet.createdAt,
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
    else if (op === 'posts' || op === 'replies' || op === 'following' || op === 'followers') {
      const page = op === 'posts' ? await client.user.timeline(id, Math.min(count, 20), cursor || undefined)
        : op === 'replies' ? await client.user.replies(id,Math.min(count,20),cursor || undefined)
        : op === 'followers' ? await client.user.followers(id,Math.min(count,100),cursor || undefined)
        : await client.user.following(id, Math.min(count, 20), cursor || undefined);
      // Reply timelines contain conversation context by other authors; do not
      // attribute those context posts to the tracked account.
      const users = op === 'following' || op === 'followers';
      const items = users ? page.list : page.list.filter(t => t.tweetBy?.id === id);
      data = {items:items.map(users ? userData : tweetData), next:cursorValue(page.next)};
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
