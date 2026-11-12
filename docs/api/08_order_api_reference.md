# Orbit Order API Reference

Base URL: https://api.orbit.example/v1
All requests go through api-gateway and require a valid access token from auth-service.

## Authentication
- Send the header: Authorization: Bearer <access_token>
- Expired tokens return HTTP 401. The client must use the refresh token to get a new one.

## Endpoints
- POST /orders: create a new order. Returns HTTP 201 with the order id.
- GET /orders/{id}: get one order and its status.
- GET /orders: list orders for the current user. Maximum 50 per page.
- POST /orders/{id}/cancel: cancel an order. Only possible before the status is "shipped".

## Order statuses
created, stock_reserved, paid, shipped, cancelled.

## Rate limits
- 100 requests per minute per user.
- Exceeding the limit returns HTTP 429 with a Retry-After header.
- Internal services are not rate limited.

## Error codes
- 400: invalid request body.
- 401: missing or expired token.
- 403: the user does not own this order.
- 404: order not found.
- 409: order cannot be cancelled because it has already shipped.
- 429: rate limit exceeded.
- 503: a dependency is down. Safe to retry after a few seconds.

## Idempotency
POST /orders accepts an Idempotency-Key header. Sending the same key twice within 24 hours returns the original order instead of creating a duplicate.
