import os
from litellm import Router

# Set up automatic model routing with LiteLLM
# This configures a router with fallback models if the primary model fails
# (e.g., due to rate limits or API downtime).

def setup_router():
    # Define your model list with the priority/fallbacks.
    # The router will attempt them in the order specified or based on the routing strategy.
    model_list = [
        {
            "model_name": "zeev-primary",
            "litellm_params": {
                "model": "groq/llama3-70b-8192",
                # "api_key": os.getenv("GROQ_API_KEY") # Usually picked up automatically if set
            }
        },
        {
            "model_name": "zeev-primary",
            "litellm_params": {
                "model": "gpt-4o-mini",
                # "api_key": os.getenv("OPENAI_API_KEY")
            }
        },
        {
            "model_name": "zeev-primary",
            "litellm_params": {
                "model": "claude-3-haiku-20240307",
                # "api_key": os.getenv("ANTHROPIC_API_KEY")
            }
        }
    ]

    # Initialize the router.
    # fallback_dict can specify fallback models if a specific model group fails.
    router = Router(
        model_list=model_list,
        routing_strategy="simple-shuffle", # Can be 'usage-based-routing', 'latency-based-routing', etc.
        set_verbose=False,
        num_retries=2,       # Number of retries across the fallbacks
        timeout=15.0         # 15 second timeout for requests
    )
    
    return router

def generate_chat(router, prompt: str):
    messages = [{"role": "user", "content": prompt}]
    try:
        # We request "zeev-primary" and LiteLLM automatically routes to Groq. 
        # If Groq fails or times out, it automatically falls back to GPT-4o-mini, then Claude Haiku.
        response = router.completion(
            model="zeev-primary", 
            messages=messages
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"All routed models failed: {e}")
        return None

if __name__ == "__main__":
    print("Setting up LiteLLM router...")
    router = setup_router()
    
    print("Testing automatic routing with a prompt...")
    # This will use the environment variables GROQ_API_KEY, OPENAI_API_KEY, etc.
    # Make sure they are exported in your environment.
    answer = generate_chat(router, "What is the capital of France? Answer in one word.")
    
    if answer:
        print(f"Response: {answer}")
