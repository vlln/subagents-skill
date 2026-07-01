from runtime import set_mock
set_mock("shell")

meta = dict(
    name='demo-workflow',
    description='Demo workflow showing TTY tree rendering with phases and agents',
    phases=[
        dict(title='Planning', detail='analyze requirements'),
        dict(title='Implementation', detail='implement features'),
        dict(title='Review', detail='review and verify'),
    ],
)

def run(agent, parallel, pipeline, phase, log, args, workflow):
    phase('Planning')
    log('Analyzing project structure...')
    plan = agent('echo "Plan: use JWT auth, refresh tokens, session store"', label='analyze')

    phase('Implementation')
    log('Implementing core features...')
    impl = agent('echo "Implemented: login, logout, token refresh endpoints"', label='implement')

    phase('Review')
    log('Reviewing implementation...')
    review = agent('echo "Review: all tests pass, security checks OK"', label='review')

    return dict(plan=plan, impl=impl, review=review)