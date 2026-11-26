import os  
from dotenv import  load_dotenv
from openai import OpenAI

load_dotenv()
client = OpenAI()

cevap = client.responses.create(

    model=os.getenv("MODEL"),
    input = "Kamu İhalesinde İKN ne demek? Tek cümleyle açıkla",

)

print(cevap.output_text)