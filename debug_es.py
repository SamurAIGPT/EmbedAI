import os

from dotenv import load_dotenv
from elasticsearch import Elasticsearch

load_dotenv()
es_password = os.getenv("ES_PASSWORD", None)

es = Elasticsearch(
    'https://160.85.252.155:9200',
    basic_auth=('elastic', es_password),
    verify_certs=False
)

res = es.search(
    index='enron',
    body={
        'query': {
            'match': {
                'parts.content': 'Kenneth'
            }
        }
    }
)

for hit in res['hits']['hits']:
    print(hit['_source']['parts'][0]['content'])