# Import Value Checker

A prototype web app for customs and port inspectors. It helps them spot vehicle imports whose declared price looks too low compared to the car's market value.

> ⚠️ A flag from this app is **never proof of fraud**. It only means the declaration **needs review** by a person.

## What it will do

1. The inspector photographs the invoice or customs document (and, optionally, the VIN plate).
2. The app reads the photo and pulls out the VIN, price, currency, make, model, year, seller and buyer.
3. The inspector checks those fields and fixes any mistakes.
4. The app validates the VIN and decodes it with the free NHTSA vPIC API.
5. It warns when the decoded vehicle doesn't match the paperwork.
6. It compares the declared price with a market value. The value comes from a mock provider for now; a real one can be plugged in later.
7. It shows a result: 🟢 GREEN / 🟡 YELLOW / 🔴 RED, labelled "needs review". The thresholds can be changed.
8. It saves every check in a history you can search by VIN and export to CSV.

## Status

🚧 Planning stage. The setup, run and deploy instructions will go here once the first working version exists.
