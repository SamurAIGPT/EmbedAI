# PrivateGPT

Create a QnA chatbot on your documents without relying on the internet by utilizing the capabilities of local LLMs. Ensure complete privacy and security as none of your data ever leaves your local execution environment. Seamlessly process and inquire about your documents even without an internet connection. Inspired from [imartinez](https://github.com/imartinez)

<img width="948" alt="pgpt" src="https://github.com/SamurAIGPT/privateGPT/assets/4326215/76e24cd4-a890-4253-bb87-098c4f1328fd">

## Contents

* [Getting started](#getting-started)
* [Requirements](#requirements)
* [How to run](#how-to-run)
* [Support](#support)
* [Data](#data)
* [Check repos](#check-repos)

## Getting started

Code is up, ⭐ (Star) the repo meanwhile to receive updates

Follow [Anil Chandra Naidu Matcha](https://twitter.com/matchaman11) & [Ankur Singh](https://twitter.com/ankur_maker) on twitter for updates

## Requirements

* Python 3.8 or later
* NodeJS v18.12.1 or later
* Minimum 16GB of memory

## How to run

1. Go to client folder and run the below commands

   ```shell
   npm install   
   ```

   ```shell
   npm run dev
   ```

2. Go to server folder and run the below commands

   ```shell
   python -m venv my_env
   ```

   ```shell
   // Linux
   source ./my_env/Scripts/activate

   // Windows
   my_env\Scripts\activate
   ```

   ```shell
   pip install -r requirements.txt
   ```

   ```shell
   python privateGPT.py
   ```

3. Open <http://localhost:3000>, click on download model to download the required model initially

4. Upload any document of your choice and click on Ingest data. Ingestion is fast

5. Now run any query on your data. Data querying is slow and thus wait for sometime

## Support

Join our discord <https://discord.gg/A6EzvsKX4u> to get support

## Data

The supported extensions for documents are:

* .csv: CSV,
* .docx: Word Document,
* .enex: EverNote,
* .eml: Email,
* .epub: EPub,
* .html: HTML File,
* .md: Markdown,
* .msg: Outlook Message,
* .odt: Open Document Text,
* .pdf: Portable Document Format (PDF),
* .pptx : PowerPoint Document,
* .txt: Text file (UTF-8),

## Check repos

* [Langchain Course](https://github.com/SamurAIGPT/langchain-course)
* [ChatGPT Developer Plugins](https://github.com/SamurAIGPT/ChatGPT-Developer-Plugins)
* [Camel AGI](https://github.com/SamurAIGPT/Camel-AutoGPT)
