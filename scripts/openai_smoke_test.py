from openai import OpenAI

client = OpenAI()

def main():
    resp = client.responses.create(
        model="gpt-5-mini",
        input="Write one sentence describing a dwarven valley without naming it."
    )
    print(resp.output_text)

if __name__ == "__main__":
    main()
