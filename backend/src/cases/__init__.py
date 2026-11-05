"""Business cases for QR Manager.

One module per business case, plus shared policies, ports, and errors. All business rules live
here. No boto3, HTTP types, or DynamoDB item shapes; AWS is reached only through ports. See
docs/DESIGN.md, sections 3 and 6.
"""