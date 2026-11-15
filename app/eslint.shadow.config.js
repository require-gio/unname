import base from './eslint.config.js';
import ts from 'typescript-eslint';
export default [
	...base,
	{
		files: ['**/*.ts', '**/*.svelte', '**/*.svelte.ts'],
		plugins: { '@typescript-eslint': ts.plugin },
		rules: {
			'@typescript-eslint/no-shadow': [
				'error',
				{
					ignoreTypeValueShadow: true,
					ignoreFunctionTypeParameterNameValueShadow: true,
					hoist: 'functions',
					builtinGlobals: true
				}
			]
		}
	}
];
