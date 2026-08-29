from fastapi import FastAPI
from pydantic import BaseModel
import ollama

app = FastAPI()

MODEL = "llama3:latest"


class Query(BaseModel):
    message: str


@app.post("/chat")
def chat(query: Query):
    try:
        response = ollama.chat(
            model=MODEL,
            messages=[{"role": "user", "content": query.message}]
        )

        return {"reply": response["message"]["content"]}

    except Exception as e:
        return {"error": str(e)}