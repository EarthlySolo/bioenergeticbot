import html
import json
import re
import sys
import time
import requests
import tweepy
import config
import logging
from profanity_check import predict


# make tags usable for twitter
def make_tag(tag: str):
    # split at occurence of nonalphanumeric
    words = re.split(r'[\W_]+', tag)
    words = [word.capitalize() for word in words]
    return '#' + ''.join(words)
    pass


ignore_categories = ["The Junkyard", "Products", "Meta", ""]

logging.basicConfig(filename='reposter.log',
                    level=logging.INFO,
                    format='%(asctime)s %(levelname)s: %(message)s',
                    datefmt='%d.%m.%y %H:%M:%S %Z'
                    )

# credentials of managing account
consumer_key = config.consumer_key
consumer_secret = config.consumer_secret
# reposter account tokens
access_token = config.access_token
access_token_secret = config.access_token_secret

# json for x rate limit reset time (epoch) and most recent posted thread id
with open('save_data.json', 'r') as reader:
    save_data = json.load(reader)

# dont run if rate limited
wait_time = save_data['reset_time'] - int(time.time())
if wait_time > 0:
    logging.info(f"Rate limited by Twitter. Rate limited for {int(wait_time / 60)} minutes.")
    logging.info(f"reset_time: {save_data['reset_time']} current_time: {int(time.time())}")
    sys.exit()

client = tweepy.Client(
    consumer_key=consumer_key, consumer_secret=consumer_secret,
    access_token=access_token, access_token_secret=access_token_secret,
    # to access x-rate-limit-remaining header. breaks response.data['id']
    # return_type=requests.Response
)
base_url = "https://bioenergetic.forum/"
api_url = base_url + "api/recent/posts/day"

# there's no way to only list new topics chronologically
# find all new topics since the last run using recent_tid
new_topic_list = []
found = False
# arbitrary limit
for page in range(1, 6):
    time.sleep(5)
    request_url = api_url + "?page={}".format(page)
    try:
        recent_posts = requests.request("GET", request_url).json()
        logging.info(f"Sent request to {request_url}")
    except json.JSONDecodeError:
        logging.error("Error decoding JSON.", exc_info=True)
        continue
    # save all the main post starting from page 1 of recent posts
    for post in recent_posts:
        if post['isMainPost']:
            if post['tid'] <= save_data['tid']:
                found = True
                break
            # if tid not found, it's a new topic, save it
            if post not in new_topic_list:
                new_topic_list.append(post)
    if found:
        break

if not new_topic_list:
    logging.info("No new topics.")

# rate_limit_remaining = 1
for topic in reversed(new_topic_list):
    title = html.unescape(topic['topic']['titleRaw'])
    author = html.unescape(topic['user']['displayname'])
    link = base_url + "topic/" + str(topic['tid'])
    category = topic['category']['name']
    tag_list = [html.unescape(t['value']) for t in topic['topic']['tags']] if len(topic['topic']['tags']) > 0 else []
    tags = " ".join([make_tag(t) for t in tag_list]) if len(tag_list) > 0 else ""
    tweet_text = """
{title}
{link}
{category} || {author}
{tags}""".format(title=title, author=author, link=link, category=category, tags=tags)
    logging.info(f"New Thread:\n{tweet_text}")
    if 1 in (pred := predict(term := [title, author] + tag_list)):
        # filter bad words
        logging.info(f"Filtered bad words in:\n {[term[i] for i in pred[:] if pred[i]]}.\n{topic['tid']} not posted.")
        continue
    if category in ignore_categories:
        # ignore category
        logging.info(f"Ignored category {category}. {topic['tid']} not posted.")
        continue
    try:
        # if rate_limit_remaining > 0:
        response = client.create_tweet(text=tweet_text)
        # print(response)
        logging.info(f"Tweet posted at https://twitter.com/bioenergeticbot/status/{response.data['id']}")
        save_data['failures'] = 0
        # if hasattr(response, "headers") and response.headers['x-rate-limit-remaining'] is not None:
        #     rate_limit_remaining = response.headers['x-rate-limit-remaining']
        #     logging.info(f"{rate_limit_remaining} tweets before rate limit.")
        time.sleep(20)
        # else:
        #     logging.error(f"No more posts possible before rate limit!")
    except tweepy.TooManyRequests as e:
        # will throw away the rest of the posts if rate limited
        # exponential backoff = base * multiplier ^ n-failures -- start at 0
        sleep_time = (int(e.response.headers["x-rate-limit-reset"]) - int(time.time())) * (
                2 ** save_data['failures']) + 1
        save_data['reset_time'] = int(e.response.headers["x-rate-limit-reset"]) + sleep_time
        save_data['failures'] = save_data['failures'] + 1
        logging.error(f"{save_data['failures']} consecutive rate limits. Waiting for {int(sleep_time / 60)} minutes."
                      + f"\nx-rate-limit-reset: {int(e.response.headers['x-rate-limit-reset'])}"
                      + f"\ncurrent time: {int(time.time())}",
                      exc_info=True)
        break
    except tweepy.HTTPException as e:
        logging.error(f"Error sending Tweet (tid {topic['tid']}).", exc_info=True)

with open('save_data.json', 'w') as writer:
    if len(new_topic_list) > 0:
        save_data['tid'] = new_topic_list[0]['tid']
        logging.info(f"Saved most recent topic id: {save_data['tid']}")
    json.dump(save_data, writer)
