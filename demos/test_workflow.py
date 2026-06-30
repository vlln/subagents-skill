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
    plan = agent('Reply "Plan: use JWT auth, refresh tokens, session store". Keep it one sentence.', label='analyze', phase='Planning')

    phase('Implementation')
    log('Implementing core features...')
    impl = agent('Reply "Implemented: login, logout, token refresh endpoints". Keep it one sentence.', label='implement', phase='Implementation')

    phase('Review')
    log('Reviewing implementation...')
    review = agent('Reply "Review: all tests pass, security checks OK". Keep it one sentence.', label='review', phase='Review')

    return dict(plan=plan, impl=impl, review=review)