import ollama
from rich import print


System_prompt='''
You are Jarvis, a smart, calm, and helpful AI assistant.
Be concise, clear, and intelligent.
'''

def chat():
    message = [{"role":"system","content":System_prompt}]
    print("[bold green]Jarvis is online.Type exit to quite.[/bold green]")

    while True:
        user_input=input("\nYou:")

        if user_input.lower()=="exit":
            print("Jarvis Goodbye.")
            break
        message.append({"role":"user","content":user_input})
        # response=ollama.chat(
        #     model = "llama3:8b-instruct-q4_K_M",
        #     messages=message
        # )
        stream=ollama.chat(
            model = "llama3:8b-instruct-q4_K_M",
            messages=message,
            stream=True
        )
        full_reply=""
        for chunk in stream:
            token = chunk['message']['content'] 
            print(token, end="", flush=True)
            full_reply += token
        message.append({"role":"assistant","contents":full_reply})
        print()
        # reply=response["message"]["content"]
        # message.append({"role":"assistant","content":reply})
        # print(f"[cyan]Jarvis:[/cyan] {reply}")
if __name__=="__main__":
    chat()
