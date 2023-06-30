from text_generation import Client


content = """
Good Evening,

Just wanted to let you know that the Board packages have gone out via Federal Express and are scheduled for an A.M. arrival.  All of the packages have been sent to your offices (except Romesh's = i2 New Hampshire office). Thanks!

Barbette Joy Watts Executive Assistant to David Becker One i2 Place, 3rd Floor 469-357-3460 (Direct) 469-357-6769 (Fax) barbette_watts@i2.com

SHEA DUGGER of I2 TECHNOLOGIES, INC sent Kenneth L. Lay of Enron Corporation a Priority Overnight FedEx Envelope.

This shipment is scheduled to be sent on 31OCT00.

The tracking number is 790388683062.

To track this shipment online click on the following link: http://www.fedex.com/cgi-bin/tracking?tracknumbers=790388683062&action=track&l anguage=english&cntry_code=us

----------------------------------------------------------------------

The attached press release was sent over PRNewswire at 6:00 a.m. today.

(See attached file: 3Q00 Sales and Earnings Release.doc)

3Q00 Sales and Earnings Release.doc

The attached press release was issued at Noon today.

(See attached file: Greenfield Expansion Release.doc)

Greenfield Expansion Release.doc
"""


prompt_pas = f"""System: Use the following pieces of context to answer the users question. 
If you don't know the answer, just say that you don't know, don't try to make up an answer.
----------------
{content}
Human: Which shipment was delayed?"""

prompt_orig = f"""
Use the following pieces of context to answer the question at the end. If you don't know the answer, just say that you don't know, don't try to make up an answer.
{content}
Question: Which shipment was delayed?
Helpful Answer:
"""


client = Client("http://dgx-a100.cloudlab.zhaw.ch:9175/")
print("prompt_orig", client.generate(prompt_orig, max_new_tokens=1000, temperature=0.01).generated_text)
print("prompt_pascal", client.generate(prompt_pas, max_new_tokens=1000, temperature=0.01).generated_text)
