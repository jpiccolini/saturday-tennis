<div class="d-grid gap-2 d-md-block mt-3">
    {% if not user_on_roster %}
        <form action="/signup" method="POST" style="display:inline;">
            <button type="submit" class="btn btn-success btn-lg" {% if strikes >= 3 %}disabled{% endif %}>
                Sign Up for This Week
            </button>
        </form>
    {% else %}
        <form action="/cancel" method="POST" style="display:inline;">
            {% if is_past_deadline %}
                <button type="submit" class="btn btn-warning btn-lg">Request Sub (Past Deadline)</button>
            {% else %}
                <button type="submit" class="btn btn-danger btn-lg">Drop Out</button>
            {% endif %}
        </form>
    {% endif %}
    <a href="/logout" class="btn btn-outline-secondary">Logout</a>
</div>
