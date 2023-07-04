from typing import List

from elasticsearch import Elasticsearch
from langchain.llms.base import LLM
from langchain.schema import BaseRetriever, Document


class ElasticSearchRetriever(BaseRetriever):

    def __init__(self, llm: LLM, es: Elasticsearch, email: str):
        super().__init__(llm)
        self.llm = llm
        self.es = es
        self.email = email

    def get_conversion_prompt(self, question: str) -> str:
        return f"Transform this question into elastic search keywords: {question}? Only list the keywords in comma " \
               f"seperated format."

    def get_relevant_documents(self, query: str) -> List[Document]:
        keywords = self.llm(self.get_conversion_prompt(query))["answer"]

        keywords = keywords.split(",")
        keywords = [keyword.strip() for keyword in keywords]
        must = [
            {
                "bool": {
                    'should': [
                        {'match': {'From': self.email}},
                        {'match': {'To': self.email}},
                        {'match': {'X-cc': self.email}},
                        {'match': {'X-bcc': self.email}},
                    ]
                }
            }
        ]

        must.extend([{'match': {'parts.content': keyword}} for keyword in keywords])

        res = self.es.search(
            index='enron',
            body={
                'query': {
                    "bool": {
                        "must": must,
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
