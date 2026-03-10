from agentweave.propagation import inject_trace_context, extract_trace_context
from agentweave.decorators import trace_agent

def nix_agent_logic():
    # Nix injecting trace context
    headers = {}
    inject_trace_context(headers)
    send_to_sub_agent(headers)  # Placeholder for sending headers to sub-agent

def max_agent_logic(headers):
    # Max extracting and linking trace context
    context = extract_trace_context(headers)
    @trace_agent(name="max_agent_logic", context=context)
    def sub_agent_task():
        print("Max is executing as part of the propagated trace.")
    sub_agent_task()

def send_to_sub_agent(headers):
    # Simulates sending context to Max
    max_agent_logic(headers)

if __name__ == "__main__":
    nix_agent_logic()