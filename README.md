🛡️ PrivateGPT
Create your own Q&A chatbot using your personal documents — without needing the internet. Powered by local LLMs (Large Language Models), PrivateGPT ensures your data never leaves your computer, giving you complete privacy and offline capability.

Inspired by imartinez

<img width="948" alt="pgpt" src="https://github.com/SamurAIGPT/privateGPT/assets/4326215/76e24cd4-a890-4253-bb87-098c4f1328fd">
📚 Contents
Getting Started

Requirements

How to Run

Support

Supported File Types

Related Repos

🚀 Getting Started
The code is ready to use! ⭐ Star this repository to stay updated with the latest changes.

Stay connected with us on Twitter for updates:
Anil Chandra Naidu Matcha & Ankur Singh

✅ Requirements
Before you begin, make sure your system has:

Python 3.8 or newer

Node.js v18.12.1 or newer

At least 16 GB of RAM

▶️ How to Run
Follow these steps to get PrivateGPT running on your local machine:

1. Start the Client (Frontend)
Open your terminal and run:

bash
Copier
Modifier
cd client
npm install
npm run dev
This will start the frontend at: http://localhost:3000

2. Start the Server (Backend)
In a new terminal window:

bash
Copier
Modifier
cd server
pip install -r requirements.txt
python privateGPT.py
3. Use the App
Open http://localhost:3000 in your browser.

Click Download Model to fetch the required language model (only needed once).

Upload a document (see supported types below).

Click Ingest Data to process the document (this step is fast).

Now you can ask questions about your uploaded document.

⏳ Data querying may be slow — please wait a moment for responses.

💬 Support
Need help or want to ask questions?
Join our community on Discord:
👉 https://discord.gg/A6EzvsKX4u

📁 Supported File Types
PrivateGPT supports the following document formats:

.csv — CSV

.docx — Word Document

.enex — Evernote Export

.eml — Email

.epub — ePub Book

.html — HTML File

.md — Markdown

.msg — Outlook Message

.odt — Open Document Text

.pdf — PDF Document

.pptx — PowerPoint

.txt — Plain Text (UTF-8)

🔗 Related Repositories
Here are some related projects you might find useful:

📘 Langchain Course

🔌 ChatGPT Developer Plugins

🤖 Camel AGI


