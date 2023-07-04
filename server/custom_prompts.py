from langchain import PromptTemplate

pascal_template = """
System: Use the following pieces of context to answer the users question. 
If you don't know the answer, just say that you don't know, don't try to make up an answer.
----------------
{context}
Human: {question}"""

prompt_pascal = PromptTemplate(template=pascal_template, input_variables=["context", "question"])
