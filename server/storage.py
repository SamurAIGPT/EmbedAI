import glob
import os
import warnings
from pathlib import Path
from typing import Any, List, Optional

from dotenv import load_dotenv
from elasticsearch import Elasticsearch
from flask import jsonify
from langchain import FAISS
from langchain.callbacks.manager import Callbacks
from langchain.docstore.document import Document
from langchain.embeddings import HuggingFaceEmbeddings
from langchain.llms.base import LLM
from langchain.schema import BaseRetriever
from langchain.text_splitter import RecursiveCharacterTextSplitter

from server.constants import USERNAME_MAIL, users
from server.file_loaders import LOADER_MAPPING

load_dotenv()
es_password = os.getenv("ES_PASSWORD", None)

if es_password is None:
    warnings.warn("Env. variable ES_PASSWORD not set, using default password for Elastic Search")


class ElasticSearchService:

    def __init__(self, llm: LLM, verbose: Optional[bool] = False):
        self.llm = llm
        self.verbose = verbose
        self.source_directory = os.environ.get('SOURCE_DIRECTORY', 'source_documents')
        self.es = Elasticsearch(
            'https://160.85.252.155:9200',
            basic_auth=('elastic', es_password),
            verify_certs=False
        )

    def get_conversion_prompt(self, question: str) -> str:
        return f"Transform this question into elastic search keywords: {question}? Only list the keywords in comma " \
               f"seperated format."

    def get_relevant_documents(self,
                               user: str,
                               query: str,
                               milliseconds_start: Optional[int] = 0,
                               milliseconds_end: Optional[int] = 99999999999999) -> List[str]:
        conversion_prompt = self.get_conversion_prompt(query)
        email = USERNAME_MAIL[user]

        if self.verbose:
            print("Conversion form query to ES keywords:")
            print(conversion_prompt)

        #[HumanMessage(content=messages[0])]
        keywords = self.llm(conversion_prompt)
        for rmk in ["\n", "\r", "\t", "Answer: ", "Keywords: "]:
            keywords = keywords.replace(rmk, "")
        keywords = keywords.split(", ")

        if self.verbose:
            print("ES keywords:", keywords)

        should = []

        for keyword in keywords:
            should.append({'match': {'parts.content': keyword}})

        must = [
            {
                "bool": {
                    'should': should,
                    "minimum_should_match": 1
                }
            },
        ]

        #must.extend([{'match': {'parts.content': keyword}} for keyword in keywords])

        body = {
            'query': {
                'bool': {
                    "must": must,
                    'filter': [
                        {
                            'bool': {
                                'should': [
                                    {'term': {'Cc.keyword': email}},
                                    {'term': {'Bcc.keyword': email}},
                                    {'term': {'From': email}},
                                    {'term': {'To': email}},
                                ]
                            }
                        },
                        {
                            'range': {'Date.$date': {
                                'gte': milliseconds_start,
                                'lte': milliseconds_end
                            }}
                        }

                    ]
                }
            }
        }

        if self.verbose:
            print("ES query:", body)

        res = self.es.search(
            index='enron',
            body=body,
            size=5
        )

        n_res = len(res['hits']['hits'])
        if n_res == 0:
            result = []
        else:
            ids = set([res['hits']['hits'][i]['_id'] for i in range(n_res)])
            result = [Path(self.source_directory) / user / f"{mid}.json" for mid in ids]

        if self.verbose:
            print("ES result:", result)

        return result


class VectorStoreService:

    def __init__(self, verbose: Optional[bool] = False):
        self.verbose = verbose
        self.persist_directory = os.environ.get('PERSIST_DIRECTORY')
        self.source_directory = os.environ.get('SOURCE_DIRECTORY', 'source_documents')
        embeddings_model_name = os.environ.get("EMBEDDINGS_MODEL_NAME")
        self.chunk_size = 2000
        self.chunk_overlap = 100
        self.max_context_len = 500
        self.embeddings = HuggingFaceEmbeddings(model_name=embeddings_model_name)
        self.db_instances = {}

    def ingest_data(self):
        document_count = 0

        for user in users:
            sd = Path(self.source_directory) / user
            pd = Path(self.persist_directory) / user / "faiss_index"

            if pd.exists():
                print(f"Found existing vectorstore for {user} in {pd}, skipping ingestion")
                self.db_instances[user] = FAISS.load_local(pd, embeddings=self.embeddings)
                continue

            # Load documents and split in chunks
            print(f"Loading documents from {sd} and store them into {pd}")

            documents = self._load_documents(sd)
            text_splitter = RecursiveCharacterTextSplitter(chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap)
            texts = text_splitter.split_documents(documents)
            print(f"Loaded {len(documents)} documents from {sd}")
            print(f"Split into {len(texts)} chunks of text (max. {self.chunk_size} characters each)")

            if len(documents) <= 0:
                continue

            document_count += len(documents)

            # Create and store locally vectorstore
            db = FAISS.from_documents(texts, embedding=self.embeddings)
            db.save_local(pd)
            self.db_instances[user] = db
        return jsonify(response=f"({document_count})", status=200)

    def get_context(self, user: str, query: str, source_docs: List[str]):
        if user not in self.db_instances:
            raise ValueError(f"User {user} not found in database. Please create index first.")

        db = self.db_instances[user]

        if self.verbose:
            print("FAISS Query:", query, "Source docs:", source_docs)

        #assert len(source_docs) <= 1, "Only one source document is supported at the moment (ES provides too many sources)"

        if len(source_docs) == 0:
            result = ""
            if self.verbose:
                print("FAISS: No source document provided, returning empty string")

        else:
            source = source_docs[0]
            if not isinstance(source, str):
                source = str(source)
            results = db.similarity_search(query, filter={"source": source}, k=100)

            tmp_result = []
            unique_result_strings = set()
            for result in results:
                if not result.page_content in unique_result_strings:
                    tmp_result.append(result)
                    unique_result_strings.add(result.page_content)
            results = tmp_result
            #greedy context filling
            tmp_result = []
            current_lent = 0
            for result in results:
                current_lent += len(result.page_content.split(' '))
                if current_lent > self.max_context_len:
                    break
                tmp_result.append(result)
            results = tmp_result

        if self.verbose:
            print("FAISS result:", results)

        return results

    def _load_single_document(self, file_path: str) -> Document:
        ext = "." + file_path.rsplit(".", 1)[-1]
        if ext in LOADER_MAPPING:
            loader_class, loader_args = LOADER_MAPPING[ext]
            loader = loader_class(file_path, **loader_args)
            return loader.load()[0]

        raise ValueError(f"Unsupported file extension '{ext}'")

    def _load_documents(self, source_dir: str) -> List[Document]:
        # Loads all documents from source documents directory
        all_files = []
        for ext in LOADER_MAPPING:
            all_files.extend(
                glob.glob(os.path.join(source_dir, f"**/*{ext}"), recursive=True)
            )
        return [self._load_single_document(file_path) for file_path in all_files]


class DataPipeline:

    def __init__(self, llm: LLM, verbose: Optional[bool] = False):
        self.llm = llm
        self.verbose = verbose
        self.vector_store_service = VectorStoreService(verbose)
        self.es_service = ElasticSearchService(llm, verbose)

    def ingest_data(self):
        return self.vector_store_service.ingest_data()

    def retrieve_context(self, query, username: str, milliseconds_start: int, milliseconds_end: int, *args, **kwargs):
        documents_es = self.es_service.get_relevant_documents(username, query, milliseconds_start, milliseconds_end)
        context = self.vector_store_service.get_context(username, query, documents_es)

        if self.verbose:
            print("Data Pipeline Context:", context)

        return context


class CustomDataRetriever(BaseRetriever):

    def __init__(self, DataPipeline: DataPipeline, username: str, milliseconds_start: int, milliseconds_end: int):
        self.pipeline = DataPipeline
        self.username = username
        self.milliseconds_start = milliseconds_start
        self.milliseconds_end = milliseconds_end

    def get_relevant_documents(
            self, query: str, *, callbacks: Callbacks = None, **kwargs: Any
    ) -> List[Document]:
        """Retrieve documents relevant to a query.
        Args:
            query: string to find relevant documents for
            callbacks: Callback manager or list of callbacks
        Returns:
            List of relevant documents
        """
        return self.pipeline.retrieve_context(query, self.username, self.milliseconds_start, self.milliseconds_end)

    async def aget_relevant_documents(
            self, query: str, *, callbacks: Callbacks = None, **kwargs: Any
    ) -> List[Document]:
        """Asynchronously get documents relevant to a query.
        Args:
            query: string to find relevant documents for
            callbacks: Callback manager or list of callbacks
        Returns:
            List of relevant documents
        """
        raise NotImplementedError()
