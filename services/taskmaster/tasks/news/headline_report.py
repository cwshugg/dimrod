# Imports
import os
import sys
from datetime import datetime
import inspect
import random

# Enable import from the parent directory
fdir = os.path.dirname(os.path.realpath(__file__))
pdir = os.path.dirname(os.path.dirname(os.path.dirname(fdir)))
if pdir not in sys.path:
    sys.path.append(pdir)

# Service imports
from task import TaskJob
from lib.config import Config, ConfigField
from lib.oracle import OracleSession
import lib.dtu as dtu
from lib.news import NewsAPI, NewsAPIConfig, NewsAPIQueryArticles

class TaskJob_News_HeadlineReport_Config(Config):
    def __init__(self):
        super().__init__()
        self.fields = [
            ConfigField("news",                 [NewsAPIConfig],        required=True),
            ConfigField("queries",              [NewsAPIQueryArticles], required=True),
            ConfigField("articles_per_query",   [int],                  required=False, default=10),
            ConfigField("telegram_chat_ids",    [list],                 required=True),
        ]

class TaskJob_News_HeadlineReport(TaskJob):
    # Overridden initialization function.
    def init(self):
        self.refresh_rate = 60 * 30
        self.config_name = os.path.basename(__file__).replace(".py", ".json")
    
    # Returns the path to where the JSON config file is expected to be.
    def get_config_path(self):
        this_file = inspect.getfile(self.__class__)
        config_dir = os.path.dirname(os.path.realpath(this_file))
        return os.path.join(config_dir, self.config_name)

    def get_config(self):
        config = TaskJob_News_HeadlineReport_Config()
        config.parse_file(self.get_config_path())
        return config
    
    # -------------------------- Telegram Interface -------------------------- #
    # Creates and returns an authenticated OracleSession with the telegram bot.
    def get_telegram_session(self):
        s = OracleSession(self.service.config.telegram)
        s.login()
        return s
    
    # Sends a message to Telegram.
    def send_message(self, chat_id: str, text: str):
        telegram_session = self.get_telegram_session()

        # create a payload and send it to Telegram to create the menu
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "markdown",
        }
        r = telegram_session.post("/bot/send/message", payload=payload)

        # we expect menu creation to always succeed
        assert telegram_session.get_response_success(r), \
               "Failed to send message via Telegram: %s" % \
               telegram_session.get_response_message(r)

        message = telegram_session.get_response_json(r)
        return message
    
    # --------------------------- Update Function ---------------------------- #
    def update(self, todoist, gcal):
        super().update(todoist, gcal)
        self.config = self.get_config()

        # is it wednesday evening? If not, don't proceed
        now = datetime.now()
        if not dtu.is_wednesday(now) or not dtu.is_evening(now):
            return False
        
        # we'll query for news articles between now and the previous successful
        # run of this taskjob
        query_timerange_start = self.get_last_success_datetime()
        query_timerange_end = now

        # set up a news API object
        newsapi = NewsAPI(self.config.news)

        # for each query, we'll gather articles and generate a telegram message
        # to send
        msg = "NEWS HEADLINES"
        for query in self.config.queries:
            query.timerange_start = query_timerange_start
            query.timerange_end = query_timerange_end
    
            # attempt to query for articles
            articles = []
            try:
                articles = newsapi.query_articles(query)
            except Exception as e:
                self.log("Something went wrong with querying the news API: %s" % e)
                return
            
            # create a message that randomly chooses some number of articles to
            # present
            msg += "%s:\n" % query.query_name
    
            # select a random number of articles and build them into the message
            articles = random.sample(articles, self.config.articles_per_query)
            for article in articles:
                msg += "• [%s](%s)\n" % (
                    article["title"],
                    article["url"]
                )

        # otherwise, send a message to all chat ids
        for chat_id in self.config.telegram_chat_ids:
            self.send_message(chat_id, msg)

        return True

