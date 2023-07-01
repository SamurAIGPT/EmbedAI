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

username = 'Ken'
keywords = ['shipment', 'delivery', 'order']

res = es.search(
    index='enron',
    body={
        'query': {
            "bool": {
                "must": [{
                    'match': {'parts.content': keywords},
                }],
                'should': [
                    {'match': {'From': username}},
                    {'match': {'To': username}},
                    {'match': {'Cc': username}},
                    {'match': {'Bcc': username}},
                ]
            }
        },
        "highlight": {
            "fields": {
                "text": {
                    "fragment_size": 300,
                    "number_of_fragments": 5
                }
            }
        },
        "min_score": 0.95
    }
)

for hit in res['hits']['hits']:
    print(hit['_source']['parts'][0]['content'])
