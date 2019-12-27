const Koa = require('koa');
const app = new Koa();

var Router = require('koa-router');
var router = new Router();

const parseData = require('./parser.js').parseData;

const setData = (results) => app.context.data = results;
parseData(setData);

router.get('/medals', async (ctx, next) => {
  ctx.body = ctx.data;
});

app
  .use(router.routes())
  .use(router.allowedMethods());

app.listen(3000);