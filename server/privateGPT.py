import glob
import os
import uuid
from typing import List

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from langchain import HuggingFaceTextGenInference
from langchain.callbacks.streaming_stdout import StreamingStdOutCallbackHandler
from langchain.chains import ConversationalRetrievalChain
from langchain.chat_models import ChatOpenAI
from langchain.docstore.document import Document
from langchain.document_loaders import (CSVLoader, EverNoteLoader, PDFMinerLoader, TextLoader, UnstructuredEPubLoader,
                                        UnstructuredEmailLoader, UnstructuredHTMLLoader, UnstructuredMarkdownLoader,
                                        UnstructuredODTLoader, UnstructuredPowerPointLoader,
                                        UnstructuredWordDocumentLoader)
from langchain.embeddings import HuggingFaceEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.vectorstores import Chroma
from langchain.llms import OpenAI

from constants import CHROMA_SETTINGS
from server.custom_prompts import prompt_pascal

app = Flask(__name__)
CORS(app)

load_dotenv()

embeddings_model_name = os.environ.get("EMBEDDINGS_MODEL_NAME")
persist_directory = os.environ.get('PERSIST_DIRECTORY')
llm = None
users = ["Ken", "Jeff", "Andrew", "Pete"]
memory = {}


class MyElmLoader(UnstructuredEmailLoader):
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


# Map file extensions to document loaders and their arguments
LOADER_MAPPING = {
    ".csv": (CSVLoader, {}),
    # ".docx": (Docx2txtLoader, {}),
    ".doc": (UnstructuredWordDocumentLoader, {}),
    ".docx": (UnstructuredWordDocumentLoader, {}),
    ".enex": (EverNoteLoader, {}),
    ".eml": (MyElmLoader, {}),
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


def load_single_document(file_path: str) -> Document:
    ext = "." + file_path.rsplit(".", 1)[-1]
    if ext in LOADER_MAPPING:
        loader_class, loader_args = LOADER_MAPPING[ext]
        loader = loader_class(file_path, **loader_args)
        return loader.load()[0]

    raise ValueError(f"Unsupported file extension '{ext}'")


def load_documents(source_dir: str) -> List[Document]:
    # Loads all documents from source documents directory
    all_files = []
    for ext in LOADER_MAPPING:
        all_files.extend(
            glob.glob(os.path.join(source_dir, f"**/*{ext}"), recursive=True)
        )
    return [load_single_document(file_path) for file_path in all_files]


@app.route('/ingest', methods=['GET'])
def ingest_data():
    # Load environment variables
    persist_directory = os.environ.get('PERSIST_DIRECTORY')
    source_directory = os.environ.get('SOURCE_DIRECTORY', 'source_documents')
    embeddings_model_name = os.environ.get('EMBEDDINGS_MODEL_NAME')

    for user in users:
        sd = f"{source_directory}/{user}"
        pd = f"{persist_directory}/{user}"
        # Load documents and split in chunks
        print(f"Loading documents from {sd} and store them into {pd}")
        chunk_size = 1000
        chunk_overlap = 50
        documents = load_documents(sd)
        text_splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        texts = text_splitter.split_documents(documents)
        print(f"Loaded {len(documents)} documents from {sd}")
        print(f"Split into {len(texts)} chunks of text (max. {chunk_size} characters each)")

        if len(documents) <= 0:
            continue

        # Create embeddings
        embeddings = HuggingFaceEmbeddings(model_name=embeddings_model_name)

        # Create and store locally vectorstore
        db = Chroma.from_documents(texts, embeddings, persist_directory=pd, client_settings=CHROMA_SETTINGS[user])
        db.persist()
        db = None
    return jsonify(response="Success")


@app.route('/get_answer', methods=['POST'])
def get_answer():
    query = request.json['query']
    memory_id = request.json["memory_id"]
    user = request.json["user"]

    print(f"Query: {query}, memory_id: {memory_id}, user: {user}")

    if llm is None:
        return "Model not downloaded", 400

    embeddings = HuggingFaceEmbeddings(model_name=embeddings_model_name)
    db = Chroma(persist_directory=f"{persist_directory}/{user}", embedding_function=embeddings,
                client_settings=CHROMA_SETTINGS[user])
    retriever = db.as_retriever()

    if memory_id is None or memory_id == "":
        memory_id = str(uuid.uuid4())
        memory[memory_id] = []

    qa = ConversationalRetrievalChain.from_llm(llm, retriever=retriever, return_source_documents=True, verbose=True,)
                                               # condense_question_prompt=prompt_pascal)
    if query is not None and query != "":
        result = qa({"question": query, "chat_history": memory[memory_id]})
        answer = result["answer"]
        memory[memory_id].append((query, result["answer"]))

        source_data = []
        for document in result['source_documents']:
            source_data.append({"name": document.metadata["source"]})

        return jsonify(query=query, answer=answer, source=source_data, memory_id=memory_id)

    return "Empty Query", 400


@app.route('/upload_doc', methods=['POST'])
def upload_doc():
    if 'document' not in request.files:
        return jsonify(response="No document file found"), 400

    document = request.files['document']
    username = request.json['username']
    if document.filename == '':
        return jsonify(response="No selected file"), 400

    filename = document.filename
    save_path = os.path.join(f'source_documents/{username}', filename)
    document.save(save_path)

    return jsonify(response="Document upload successful")


def load_model():
    global llm
    callbacks = [StreamingStdOutCallbackHandler()]
    llm = HuggingFaceTextGenInference(
        inference_server_url="http://dgx-a100.cloudlab.zhaw.ch:9175/",
        max_new_tokens=512,
        top_k=10,
        top_p=0.95,
        typical_p=0.95,
        temperature=0.001,
        repetition_penalty=1.03,
        callbacks=callbacks,
    )
    #llm = ChatOpenAI(temperature=0, model='gpt-3.5-turbo')


if __name__ == "__main__":
    load_model()
    app.run(host="0.0.0.0", port=8888, debug=False)
