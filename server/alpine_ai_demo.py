import uuid

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from langchain import HuggingFaceTextGenInference
from langchain.callbacks.streaming_stdout import StreamingStdOutCallbackHandler
from langchain.chains import ConversationalRetrievalChain
from langchain.chat_models import ChatOpenAI

from server.custom_prompts import prompt_pascal
from server.storage import CustomDataRetriever, DataPipeline

app = Flask(__name__)
CORS(app)

load_dotenv()

VERBOSE = True
llm = None
llm_name = "Falcon-40B"
data_pipeline = None

memory = {}


@app.route('/ingest', methods=['GET'])
def ingest_data():
    return data_pipeline.ingest_data()


@app.route('/get_answer', methods=['POST'])
def get_answer():
    global llm_name

    query = request.json['query']
    memory_id = request.json["memory_id"]
    user = request.json["user"]
    model_name = request.json["modelname"]

    print(f"Query: {query}, memory_id: {memory_id}, user: {user}, model: {model_name}")

    if llm_name != model_name:
        load_components()
        llm_name = model_name

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

    qa = ConversationalRetrievalChain.from_llm(llm, retriever=CustomDataRetriever(data_pipeline, user),
                                               # chain_type="map_rerank",
                                               return_source_documents=True, verbose=VERBOSE,
                                               combine_docs_chain_kwargs={"prompt": prompt})
    if query is not None and query != "":
        result = qa({"question": query, "chat_history": memory[memory_id]})
        answer = result["answer"]

        if answer.startswith("System: "):
            answer = answer[len("System: "):]

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

    return jsonify(response="No yet implemented"), 400


def load_components():
    global llm
    global llm_name
    global data_pipeline

    if llm_name == "Falcon-40B" or llm_name == "Swiss-Finish":
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
    elif llm_name == "GPT-3.5-Turbo":
        llm = ChatOpenAI(temperature=0, model='gpt-3.5-turbo')

    else:
        raise ValueError(f"Unknown model name: {llm_name}")

    data_pipeline = DataPipeline(llm, verbose=VERBOSE)
    data_pipeline.ingest_data()


if __name__ == "__main__":
    app.app_context().push()
    load_components()
    app.run(host="0.0.0.0", port=8888, debug=False)
