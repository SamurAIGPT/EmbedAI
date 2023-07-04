import glob
import json
import os
import warnings
from typing import List

from dotenv import load_dotenv
from elasticsearch import Elasticsearch
from flask import jsonify
from langchain.docstore.document import Document
from langchain.document_loaders import (CSVLoader, EverNoteLoader, PDFMinerLoader, TextLoader, UnstructuredEPubLoader,
                                        UnstructuredEmailLoader, UnstructuredHTMLLoader, UnstructuredMarkdownLoader,
                                        UnstructuredODTLoader, UnstructuredPowerPointLoader,
                                        UnstructuredWordDocumentLoader)
from langchain.embeddings import HuggingFaceEmbeddings
from langchain.llms.base import LLM
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.vectorstores import Chroma
from langchain.vectorstores.base import VectorStoreRetriever

from server.constants import CHROMA_SETTINGS, users
from server.custom_retriever import ElasticSearchRetriever

load_dotenv()
es_password = os.getenv("ES_PASSWORD", None)

if es_password is None:
    warnings.warn("Env. variable ES_PASSWORD not set, using default password for Elastic Search")


class CustomElmLoader(UnstructuredEmailLoader):
    """Wrapper to fallback to text/plain when default does not work"""

    def load(self) -> List[Document]:
        """Wrapper adding fallback for elm without html"""
        try:
            try:
                doc = UnstructuredEmailLoader.load(self)
            except ValueError as e:
                if 'text/html content not found in email' in str(e):
                    # Try plain text
                    self.unstructured_kwargs["content_source"] = "text/plain"
                    doc = UnstructuredEmailLoader.load(self)
                else:
                    raise
        except Exception as e:
            # Add file_path to exception message
            raise type(e)(f"{self.file_path}: {e}") from e

        return doc


class DataService:

    def ingest_data(self):
        raise NotImplementedError()

    def upload_doc(self, doc: Document, username: str):
        raise NotImplementedError()

    def get_retriever(self, username: str):
        raise NotImplementedError()


class ElasticSearchService(DataService):

    def __init__(self, llm: LLM):
        self.llm = llm
        self.es = Elasticsearch(
            'https://160.85.252.155:9200',
            basic_auth=('elastic', es_password),
            verify_certs=False
        )

    def ingest_data(self):
        return jsonify(response="Not necessary for Elastic Search", status=400)

    def upload_doc(self, doc: Document, username: str):
        return jsonify(response="Not supported for Elastic Search", status=400)

    def get_retriever(self, username: str):
        return ElasticSearchRetriever(self.llm, self.es, username)


class VectorStoreService(DataService):
    loader_mapping = {
        ".csv": (CSVLoader, {}),
        # ".docx": (Docx2txtLoader, {}),
        ".doc": (UnstructuredWordDocumentLoader, {}),
        ".docx": (UnstructuredWordDocumentLoader, {}),
        ".enex": (EverNoteLoader, {}),
        ".eml": (CustomElmLoader, {}),
        ".epub": (UnstructuredEPubLoader, {}),
        ".html": (UnstructuredHTMLLoader, {}),
        ".md": (UnstructuredMarkdownLoader, {}),
        ".odt": (UnstructuredODTLoader, {}),
        ".pdf": (PDFMinerLoader, {}),
        ".ppt": (UnstructuredPowerPointLoader, {}),
        ".pptx": (UnstructuredPowerPointLoader, {}),
        ".txt": (TextLoader, {"encoding": "utf8"}),
        # Add more mappings for other file extensions and loaders as needed
    }

    def __init__(self):
        self.persist_directory = os.environ.get('PERSIST_DIRECTORY')
        self.source_directory = os.environ.get('SOURCE_DIRECTORY', 'source_documents')
        embeddings_model_name = os.environ.get("EMBEDDINGS_MODEL_NAME")
        self.chunk_size = 1000
        self.chunk_overlap = 50
        self.embeddings = HuggingFaceEmbeddings(model_name=embeddings_model_name)

    def ingest_data(self):
        for user in users:
            sd = f"{self.source_directory}/{user}"
            pd = f"{self.persist_directory}/{user}"
            # Load documents and split in chunks
            print(f"Loading documents from {sd} and store them into {pd}")

            documents = self._load_documents(sd)
            text_splitter = RecursiveCharacterTextSplitter(chunk_size=self.chunk_size, chunk_overlap=self.chunk_overlap)
            texts = text_splitter.split_documents(documents)
            print(f"Loaded {len(documents)} documents from {sd}")
            print(f"Split into {len(texts)} chunks of text (max. {self.chunk_size} characters each)")

            if len(documents) <= 0:
                continue

            # Create and store locally vectorstore
            db = Chroma.from_documents(texts, embedding=self.embeddings, persist_directory=pd,
                                       client_settings=CHROMA_SETTINGS[user])
            db.persist()
            # db.similarity_search("Test", filter={"source": "source_documents/Ken/all_documents/1.eml"})
        return jsonify(response="Success")

    def upload_doc(self, doc: json, username: str):
        filename = doc.filename
        save_path = os.path.join(f'source_documents/{username}', filename)
        doc.save(save_path)
        return jsonify(response="Document upload successful")

    def get_retriever(self, user: str) -> VectorStoreRetriever:
        db = Chroma(persist_directory=f"{self.persist_directory}/{user}", embedding_function=self.embeddings,
                    client_settings=CHROMA_SETTINGS[user])
        return db.as_retriever()

    def _load_single_document(self, file_path: str) -> Document:
        ext = "." + file_path.rsplit(".", 1)[-1]
        if ext in self.loader_mapping:
            loader_class, loader_args = self.loader_mapping[ext]
            loader = loader_class(file_path, **loader_args)
            return loader.load()[0]

        raise ValueError(f"Unsupported file extension '{ext}'")

    def _load_documents(self, source_dir: str) -> List[Document]:
        # Loads all documents from source documents directory
        all_files = []
        for ext in self.loader_mapping:
            all_files.extend(
                glob.glob(os.path.join(source_dir, f"**/*{ext}"), recursive=True)
            )
        return [self._load_single_document(file_path) for file_path in all_files]
