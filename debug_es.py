from elasticsearch import Elasticsearch

es = Elasticsearch(
    'https://160.85.252.155:9200',
    basic_auth=('elastic', '0MhYFBMX9o7aLOV2b3J0'),
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