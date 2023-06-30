from typing import List

from elasticsearch import Elasticsearch
from langchain.llms.base import LLM
from langchain.schema import BaseRetriever, Document


class ElasticSearchRetriever(BaseRetriever):

    def __init__(self, llm: LLM, es: Elasticsearch, user: str):
        super().__init__(llm)
        self.llm = llm
        self.es = es
        self.user = user

    def get_conversion_prompt(self, question: str) -> str:
        return f"Transform this question into elastic search keywords: {question}? Only list the keywords in comma " \
               f"seperated format."

    def get_relevant_documents(self, query: str) -> List[Document]:
        keywords = self.llm(self.get_conversion_prompt(query))["answer"]
        res = self.es.search(
            index='enron',
            body={
                'query': {
                    'match': {
                        'parts.content': self.user
                    }
                }
            }
        )
