import html
import json
import re
import sys
import time
import requests
import tweepy
from config import ignore_categories, consumer_key, consumer_secret, access_token, access_token_secret
import logging
from profanity_check import predict


logging.basicConfig(filename='reposter.log',
                    level=logging.INFO,
                    format='%(asctime)s %(levelname)s: %(message)s',
                    datefmt='%d.%m.%y %H:%M:%S %Z'
                    )

base_url = "https://bioenergetic.forum/"
api_url = base_url + "api/recent/posts/day"


def make_tag(tag: str):
    """make tags usable for twitter"""
    # split at occurrence of non-alphanumeric
    words = re.split(r'[\W_]+', tag)
    words = [word.capitalize() for word in words]
    return '#' + ''.join(words)


def search_topics() -> list:
    """need forum account for search"""
    search_url = base_url + "api/search?in=titles&term=&matchWords=all&by=&categories=&searchChildren=false&hasTags" \
                            "=&replies=&repliesFilter=atleast&timeFilter=newer&timeRange=86400&sortBy=topic.timestamp" \
                            "&sortDirection=desc&showAs=topics&page=1"
    login_data = {"username": "",
                  "password": ""}
    with requests.Session() as s:
        s.post(base_url + "api/v3/utilities/login", json=login_data)
        r = requests.post(search_url).json()
        posts = r["posts"]
    pass


def recent_posts() -> list:
    # find all new topics since the last run using save_data['tid']
    new_topic_list = []
    found = False
    for page in range(1, 6):
        time.sleep(5)
        request_url = api_url + "?page={}".format(page)
        try:
            posts = requests.request("GET", request_url).json()
            logging.info(f"Sent request to {request_url}")
        except json.JSONDecodeError:
            logging.error("Error decoding JSON.", exc_info=True)
            raise
        # save all the main posts starting from page 1 of recent posts
        for post in posts:
            if post['isMainPost']:
                if post['tid'] <= save_data['tid']:
                    found = True
                    break
                # if tid not found, it's a new topic, save it
                if post not in new_topic_list:
                    new_topic_list.append(post)
        if found:
            break
    return new_topic_list


# json for x-rate-limit-reset time (epoch) and most recently posted topic id
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
    # to access x-rate-limit-remaining header. probably breaks access to response.data['id']
    # return_type=requests.Response
)

new_topics = recent_posts()
if not new_topics:
    logging.info("No new topics.")

for topic in reversed(new_topics):
    title = html.unescape(topic['topic']['titleRaw'])
    author = html.unescape(topic['user']['displayname'])
    link = base_url + "topic/" + str(topic['tid'])
    category = html.unescape(topic['category']['name'])
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
        logging.info(f"Ignored category {category}. {topic['tid']} not posted.")
        continue
    try:
        # if rate_limit_remaining > 0:
        response = client.create_tweet(text=tweet_text)
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
    if len(new_topics) > 0:
        save_data['tid'] = new_topics[0]['tid']
        logging.info(f"Saved most recent topic id: {save_data['tid']}")
    json.dump(save_data, writer)
