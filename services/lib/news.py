# This module implements a wrapper around the News API unofficial Python SDK.
#
# https://newsapi.org/
# https://github.com/mattlisiv/newsapi-python

# Imports
import os
import sys
from datetime import datetime

# Enable import from the parent directory
fdir = os.path.dirname(os.path.realpath(__file__))
pdir = os.path.dirname(fdir)
if pdir not in sys.path:
    sys.path.append(pdir)

# Local imports
from lib.config import Config, ConfigField
import lib.dtu as dtu

# News API imports
from newsapi import NewsApiClient

# Used to configure the `NewsAPI` object.
class NewsAPIConfig(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("api_key",          [str],  required=True),
        ]

# Used to construct a query for news articles.
class NewsAPIQueryArticles(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("query_name",       [str],  required=False, default=None),
            ConfigField("query",            [str],  required=False, default=None),
            ConfigField("language",         [str],  required=False, default="en"),
            ConfigField("sources",          [list], required=False, default=None),
            ConfigField("timerange_start",  [datetime], required=False, default=None),
            ConfigField("timerange_end",    [datetime], required=False, default=None),
            ConfigField("sort_by",          [str],  required=False, default="relevancy"),
            ConfigField("max_articles",     [int],  required=False, default=100),
        ]

    def sources_to_str(self):
        if self.sources is None:
            return None
        return ",".join(self.sources)

# Used to construct a query for news sources.
class NewsAPIQuerySources(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("query_name",   [str],  required=False, default=None),
            ConfigField("country_code", [str],  required=False, default="us"),
            ConfigField("language",     [list], required=False, default="en"),
            ConfigField("category",     [list], required=False, default=None)
        ]

# The main news API object.
class NewsAPI:
    def __init__(self, config: NewsAPIConfig):
        self.config = config

    def api(self):
        return NewsApiClient(api_key=self.config.api_key)
    
    # Queries the API for news providers/sources and returns them.
    def query_sources(self, query: NewsAPIQuerySources):
        api = self.api()
        args = {
            "country": query.country_code,
            "language": query.language,
            "category": query.category
        }
        result = api.get_sources(**args)

        # if the query failed, throw an exception
        if result["status"].lower().strip() != "ok":
            msg = "Failed to query for news sources (status code: %s): %s" % (
                "???" if "code" not in result else result["code"],
                "???" if "message" not in result else result["message"],
            )
            raise Exception(msg)
        
        # otherwise, extract the sources and return the list
        return result["sources"]
    
    # Queries the API for articles and returns them.
    def query_articles(self, query: NewsAPIQueryArticles):
        api = self.api()
        args = {
            "q": query.query,
            "language": query.language,
            "sources": query.sources_to_str(),
            "from_param": query.timerange_start,
            "to": query.timerange_end,
            "sort_by": query.sort_by,
        }

        # repeatedly poll the API until all results have been collected
        all_articles = []
        total_amount = None
        page = 1
        while True:
            # if we've collected the configured maximum amount, break out of
            # the loop
            all_articles_len = len(all_articles)
            if all_articles_len >= query.max_articles:
                break

            result = api.get_everything(**args, page=page)
            
            # if the query failed, throw an exception
            if result["status"].lower().strip() != "ok":
                msg = "Failed to query for news articles (status code: %s): %s" % (
                    "???" if "code" not in result else result["code"],
                    "???" if "message" not in result else result["message"],
                )
                raise Exception(msg)

            # otherwise, store at the "totalResults" field if this is the first
            # request we've executed
            if total_amount is None:
                total_amount = result["totalResults"]

            # append each article to the `all_articles` array, capping it off
            # at the configured maximum
            articles = result["articles"]
            articles_len = len(articles)
            remaining_room = query.max_articles - all_articles_len
            all_articles += articles[0:remaining_room]

            # if NO articles were returned, then we're done
            if articles_len == 0:
                break
            
            # are there more articles to retrieve on the next "page"? If so,
            # update `page` to point to the next page number
            if articles_len < total_amount:
                page += 1
            # otherwise, there are no more pages to go through; we're done looping
            else:
                break
        
        # otherwise, extract the sources and return the list
        return all_articles

