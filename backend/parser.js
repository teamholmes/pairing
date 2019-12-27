const csv = require('csv-parser')
const fs = require('fs')
const results = [];

const parseData = function(cb) {
  fs.createReadStream('../fixtures/dummy_data.csv')
  .pipe(csv())
  .on('data', (data) => results.push(data))
  .on('end', () => {
    cb(results);
  });
}

module.exports.parseData = parseData;
