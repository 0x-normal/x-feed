import assert from 'node:assert/strict';
import {cursorValue, safeError, tweetData, userData, mediaData} from './bridge.mjs';
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
const fullCaption='First paragraph\n\nSecond paragraph with the complete ending.';
const original={...post,fullText:fullCaption};
const repost=tweetData({...post,id:'2',fullText:'RT @target: First paragraph...',retweetedTweet:original});
assert.equal(repost.text,fullCaption);
assert.equal(repost.context.original.text,fullCaption);
const longQuote={...post,fullText:'Short preview...',raw:{note_tweet:{note_tweet_results:{result:{text:fullCaption}}}},quoted:original};
assert.equal(tweetData(longQuote).text,fullCaption);
assert.equal(tweetData(longQuote).context.original.text,fullCaption);
assert.equal(tweetData({...post,retweetedTweet:longQuote}).context.original.quoted.text,fullCaption);
assert.equal(tweetData({...post,fullText:'RT preview only',retweetedTweet:{id:'3'}}).text,'RT preview only');
console.log('Cursor, error, and activity classification contracts passed');

const photo={type:'photo',media_url_https:'https://pbs.twimg.com/media/photo.jpg',ext_alt_text:'A mountain <sunset>'};
const video={type:'video',media_url_https:'https://pbs.twimg.com/media/poster.jpg',video_info:{variants:[
  {content_type:'application/x-mpegURL',url:'https://video.twimg.com/stream.m3u8'},
  {content_type:'video/mp4',bitrate:256000,url:'https://video.twimg.com/low.mp4'},
  {content_type:'video/mp4',bitrate:832000,url:'https://video.twimg.com/high.mp4'},
  {content_type:'video/mp4',bitrate:999999,url:'https://evil.test/private.mp4'}]}};
const attached={...post,raw:{legacy:{extended_entities:{media:[photo,video,{...video,type:'animated_gif'}]}}}};
const attachments=mediaData(attached);
assert.deepEqual(attachments.map(m=>m.type),['photo','video','gif']);
assert.equal(attachments[0].alt,'A mountain <sunset>');
assert.equal(attachments[1].url,'https://video.twimg.com/high.mp4');
assert.equal(attachments[2].poster,'https://pbs.twimg.com/media/poster.jpg');
assert.deepEqual(tweetData(attached).context.media,attachments);
const mediaRepost=tweetData({...post,retweetedTweet:{...attached,quoted:attached}});
assert.deepEqual(mediaRepost.context.media,[]);
assert.deepEqual(mediaRepost.context.original.media,attachments);
assert.deepEqual(mediaRepost.context.original.quoted.media,attachments);
assert.deepEqual(tweetData({...attached,quoted:attached}).context.original.media,attachments);
assert.equal(mediaData({media:[{type:'GIF',url:'https://video.twimg.com/loop.mp4'}]})[0].type,'gif');
assert.deepEqual(mediaData({media:[null,{type:'PHOTO',url:'javascript:alert(1)'},{type:'VIDEO',url:'https://video.twimg.com.evil.test/a.mp4'}]}),[]);
assert.deepEqual(mediaData({media:{}}),[]);
assert.equal(mediaData({...post,raw:{legacy:{extended_entities:{media:[{...video,video_info:{variants:[]}}]}}}})[0].url,'');
console.log('Photo, GIF, video, nested media, and URL validation passed');
