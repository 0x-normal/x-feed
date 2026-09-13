import assert from 'node:assert/strict';
import {cursorValue, safeError, tweetData, userData} from './bridge.mjs';
const profile={id:'42',userName:'example',fullName:'Example',description:'Builder & researcher',followersCount:12345,followingsCount:50};
assert.equal(userData(profile).description,'Builder & researcher');
assert.equal(userData(profile).followers_count,12345);
assert.equal(userData({...profile,followersCount:0}).followers_count,0);
assert.equal(userData({...profile,followersCount:undefined}).followers_count,null);
assert.equal(cursorValue('next-page'), 'next-page');
assert.equal(cursorValue({value:'older-format'}), 'older-format');
assert.equal(cursorValue(''), '');
assert.equal(cursorValue(undefined), '');
assert.deepEqual(safeError({response:{status:429,headers:{'x-rate-limit-reset':'2000000000'}}}),
  {kind:'TooManyRequests',reset:2000000000});
assert.deepEqual(safeError({status:200,details:[{code:64,message:'SECRET'}]}),
  {kind:'AccountSuspended',reset:null});
assert.deepEqual(safeError({response:{status:403,data:'SECRET'}}),{kind:'Forbidden',reset:null});
const post={id:'1',fullText:'Hello',createdAt:'2026-09-13T00:00:00Z',tweetBy:{userName:'target',fullName:'Target'}};
assert.equal(tweetData(post).kind,'post');
assert.equal(tweetData({...post,replyTo:'2'}).kind,'reply');
assert.equal(tweetData({...post,quoted:post}).kind,'quote');
assert.equal(tweetData({...post,retweetedTweet:post,quoted:post}).kind,'repost');
assert.equal(tweetData({...post,quoted:post}).context.original.author,'target');
assert.equal(tweetData(post).published_at,1789257600);
console.log('Cursor, error, and activity classification contracts passed');
