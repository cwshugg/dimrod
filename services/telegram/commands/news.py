# Implements the /news bot command.

# Imports
import os
import sys
import re
from datetime import datetime
import random

# Enable import from the parent directory
pdir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.oracle import OracleSession
import lib.dtu as dtu
from lib.news import NewsAPI, NewsAPIConfig, NewsAPIQuerySources, \
                     NewsAPIQueryArticles


# =================================== Main =================================== #
def command_news(service, message, args: list):
    # decide on a time range from which we'll poll articles. By default, we'll
    # do the past day
    timerange_end = datetime.now()
    timerange_start = dtu.add_days(timerange_end, -1)

    # were keywords provided? If so, we'll interpret these as individual query
    # words
    keywords = []
    if len(args) > 1:
        keywords += args[1:]
    keywords_len = len(keywords)

    # create a news API object
    newsapi = NewsAPI(service.config.news)

    # query for sources
    sources_query = NewsAPIQuerySources()
    sources_query.init_defaults()
    sources = []
    try:
        sources = newsapi.query_sources(sources_query)
    except Exception as e:
        msg = "Something went wrong with querying the news API.\n%s" % e
        service.send_message(message.chat.id, msg, parse_mode="HTML")
        return

    # if the very first keyword is "_sources", we'll send a list of all news
    # sources that are available
    if keywords_len > 0 and keywords[0].lower().strip() == "_sources":
        msg = "Available news sources:\n"
        for source in sources:
            msg += "• %s (<code>%s</code>)\n" % (
                source["name"],
                source["id"]
            )
        service.send_message(message.chat.id, msg, parse_mode="HTML")
        return

    # if there are no keywords, randomly select a default news query and query
    # for some articles
    if keywords_len == 0:
        query = random.choice(service.config.news_default_queries)
        query.timerange_start = timerange_start
        query.timerange_end = timerange_end

        # attempt to query for articles
        articles = []
        try:
            articles = newsapi.query_articles(query)
        except Exception as e:
            msg = "Something went wrong with querying the news API.\n%s" % e
            service.send_message(message.chat.id, msg, parse_mode="HTML")
            return
        
        # create a message that randomly chooses some number of articles to
        # present
        msg = "%s\n" % query.query_name

        # select a random number of articles and build them into the message
        articles = random.sample(articles, 10)
        for article in articles:
            msg += "• [%s](%s)\n" % (
                article["title"],
                article["url"]
            )
        
        # send the message
        service.send_message(message.chat.id, msg, parse_mode="markdown")
        return

    # TODO - use keywords to build custom query and run it
    msg = "TODO - Add support for keyword querying."
    service.send_message(message.chat.id, msg, parse_mode="markdown")


