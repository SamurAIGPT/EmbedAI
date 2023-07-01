import uuid

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from langchain import HuggingFaceTextGenInference
from langchain.callbacks.streaming_stdout import StreamingStdOutCallbackHandler
from langchain.chains import ConversationalRetrievalChain
from langchain.llms import OpenAI
from server.custom_prompts import prompt_pascal
from server.storage import VectorStoreService

app = Flask(__name__)
CORS(app)

load_dotenv()

llm = None
storage_service = None

memory = {}


@app.route('/ingest', methods=['GET'])
def ingest_data():
    return storage_service.ingest_data()


@app.route('/get_answer', methods=['POST'])
def get_answer():
    query = request.json['query']
    memory_id = request.json["memory_id"]
    user = request.json["user"]
    model_name = request.json["modelname"]

    print(f"Query: {query}, memory_id: {memory_id}, user: {user}")

    if llm is None:
        return "Model not downloaded", 400

    if model_name is None or model_name == "":
        return "Model not selected", 400

    if memory_id is None or memory_id == "":
        memory_id = str(uuid.uuid4())
        memory[memory_id] = []

    prompt = None  # Default prompt
    if model_name == "Swiss-Finish":
        prompt = prompt_pascal

    qa = ConversationalRetrievalChain.from_llm(llm, retriever=storage_service.get_retriever(user),
                                               return_source_documents=True, verbose=True,
                                               combine_docs_chain_kwargs={"prompt": prompt})
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

    return storage_service.upload_doc(document, username)


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
    # llm = ChatOpenAI(temperature=0, model='gpt-3.5-turbo')


def load_storage_service():
    global storage_service
    storage_service = VectorStoreService()


if __name__ == "__main__":
    load_model()
    load_storage_service()
    app.run(host="0.0.0.0", port=8888, debug=False)
