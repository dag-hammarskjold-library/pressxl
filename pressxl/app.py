from flask import Flask, render_template, request, abort, jsonify, Response, url_for
from bs4 import BeautifulSoup
from readability import Document
from requests import get
from bson.objectid import ObjectId
from nltk.tokenize import sent_tokenize
from math import ceil
from .config import Config
import feedparser, boto3, re, time, os, pymongo

app = Flask(__name__)
collection = Config.DB.pressReleases
deploy_context = os.environ.get('DEPLOY_CONTEXT','')

class Pagination(object):
    def __init__(self, page, per_page, total_count):
        self.page = page
        self.per_page = per_page
        self.total_count = total_count

    @property
    def pages(self):
        return int(ceil(self.total_count / float(self.per_page)))
    
    @property
    def has_prev(self):
        return self.page > 1

    @property
    def has_next(self):
        return self.page < self.pages

    def iter_pages(self, left_edge=2, left_current=2, right_current=2, right_edge=2):
        last = 0
        for num in range(1, self.pages + 1):
            if num <= left_edge or \
                (num > self.page - left_current - 1 and num < self.page + right_current) or \
                num > self.pages - right_edge:
                if last + 1 != num:
                    yield None
                yield num
                last = num

@app.route('/', defaults={'page': 1})
@app.route('/page/<int:page>')
def index(page):
    #collection = Config.DB.pressReleases
    page_size = Config.RPP
    page_num = page
    skips = page_size * (page_num - 1)

    lang=request.args.get('lang','en')
    
    records = collection.find({},{'link', 'title', 'summary', 'published'}).skip(skips).limit(page_size).sort([('published',pymongo.DESCENDING), ('_id', pymongo.DESCENDING)])
    pagination = Pagination(page, page_size, records.count())
    return_records = []
    for record in records:
        return_records.append({
            'id': str(record['_id']), 
            'link': record['link'], 
            'title': record['title'],
            'summary': record['summary'],
            'published': record['published']
        })
    return render_template('index.html', records=return_records, deploy_context=deploy_context, pagination=pagination, lang=lang)

def url_for_other_page(page):
    args = request.view_args.copy()
    args['page'] = page
    return url_for(request.endpoint, **args)

app.jinja_env.globals['url_for_other_page'] = url_for_other_page

@app.route('/id/<id>')
def get_by_id(id):
    lang = request.args.get('lang','en')
    record = collection.find_one({'_id': ObjectId(id)})
    return render_template('record.html', record=record, lang=lang, deploy_context=deploy_context)

@app.route('/update', methods=['POST'])
def update():
    if request.method == 'POST':
        token = request.headers.get('token')
        if token != Config.UPDATE_TOKEN:
            abort(403)
        translate = boto3.client(
            service_name='translate', 
            aws_access_key_id=Config.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=Config.AWS_SECRET_ACCESS_KEY, 
            region_name='us-east-1'
        )
        feed = feedparser.parse(Config.EN_FEED_URL)
        #print(feed.entries)
        new_entries = 0
        for entry in list(reversed(feed.entries)):
            record = collection.find_one({'link':entry.link})
            if not record:
                response = get(entry.link)
                doc = Document(response.text)
                entry['body'] = {}
                entry['body']['en'] = '<div><h1>' + doc.short_title() + '</h1>' + re.sub(u'\xa0',' ', doc.summary(html_partial=True)) + '</div>'
                text = sent_tokenize(entry['body']['en'])
                for language in Config.TARGET_LANGUAGES:
                    #print(translate)
                    translated_text = []
                    print("Translating %s sentences." % len(text))
                    for sentence in text:
                        translated_sentence = translate.translate_text(
                            Text = sentence,
                            SourceLanguageCode=Config.SOURCE_LANGUAGE, 
                            TargetLanguageCode=language
                        )
                        translated_text.append(translated_sentence['TranslatedText'])
                    entry['body'][language] = ' '.join(translated_text)
                    #time.sleep(3)
                #print(entry['body'])
                collection.insert_one(entry)
                new_entries += 1
        return Response("OK. Processed %s entries, %s new." % (len(feed.entries), new_entries))
        #return render_template('index.html', entries=feed.entries)
    else:
        abort(403)